"""run_loop.py — 三個可靠性保證的測試：逾時、修復器失敗歸因、地端先試修分層。

對應 run_loop.py docstring 的「三個可靠性保證」。這些是 review 抓到的實際缺陷：
裸的 subprocess.run 沒 timeout（hang 住無聲卡死）、Codex stderr 被丟掉
（no-progress 誤報成「測試修不動」）、以及沒有便宜的地端先試修層。
"""
import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rl = _load("run_loop", "tools/run_loop.py")

# 跨平台可用的假指令（Windows 無 sleep(1) 語意一致的 shell builtin）
PY = sys.executable
SLEEP = f'"{PY}" -c "import time; time.sleep(30)"'
FAIL = f'"{PY}" -c "import sys; sys.stderr.write(\'boom-detail\'); sys.exit(7)"'
OK = f'"{PY}" -c "print(\'fine\')"'


# ── 保證 1：一律有逾時，且逾時不拋例外 ──────────────────────────────────────
def test_run_returns_timed_out_instead_of_raising(tmp_path):
    r = rl._run(SLEEP, str(tmp_path), timeout=1)
    assert r.timed_out is True
    assert r.ok is False
    assert r.returncode == -1


def test_run_captures_stderr_on_failure(tmp_path):
    r = rl._run(FAIL, str(tmp_path), timeout=30)
    assert r.ok is False
    assert r.returncode == 7
    assert "boom-detail" in r.stderr


def test_run_ok_on_success(tmp_path):
    r = rl._run(OK, str(tmp_path), timeout=30)
    assert r.ok is True
    assert "fine" in r.stdout


def test_as_text_normalises_bytes_and_none():
    assert rl._as_text(None) == ""
    assert rl._as_text(b"abc") == "abc"
    assert rl._as_text("abc") == "abc"


def _args(**kw) -> Namespace:
    base = dict(mode="test", phase="6", run_id="run-test", max_iters=2, patience=2,
                max_tokens=None, workdir=None, review_cmd=OK, fix_cmd=OK,
                compile_cmd=None, fix_retries=1,
                review_timeout=30, fix_timeout=30,
                local_fix_cmd=None, max_local_attempts=0,
                reviewer_model=None, fixer_model=None, available=None)
    base.update(kw)
    return Namespace(**base)


def test_review_timeout_is_a_defect_never_a_pass(tmp_path, monkeypatch):
    """review 逾時若被當成「沒有失敗」就會假裝通過——必須合成缺陷。"""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    out = rl.run(_args(review_cmd=SLEEP, review_timeout=1, max_iters=1))
    assert out["status"] != "resolved"
    assert any("review:timeout" in d for d in out["final_defects"])


# ── 保證 2：修復器失敗要能歸因，不能誤報成 no_progress ──────────────────────
def test_fixer_failure_is_counted_and_attributed(tmp_path, monkeypatch):
    """review 一直有失敗、fixer 一直非零 exit → reason 必須點名 fixer_failed。"""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    failing_review = f'"{PY}" -c "print(\'FAILED tests/t.py::a\'); raise SystemExit(1)"'
    out = rl.run(_args(review_cmd=failing_review, fix_cmd=FAIL, max_iters=2, patience=2))
    assert out["status"] == "escalated"
    assert out["fixer_failures"] >= 1
    assert "fixer_failed" in out["reason"]
    assert "boom-detail" in out["fixer_last_error"]


def test_fixer_stderr_reaches_next_round_prompt(tmp_path, monkeypatch):
    """失敗訊息要寫進 defects_file，否則下一輪 Codex 看不到「上一輪為何沒改到」。"""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    seen: list[str] = []
    real_run = rl._run

    def spy(cmd, cwd, timeout):
        # fix 指令執行前，記錄當時 defects_file 的內容
        if "read-defects" in cmd:
            path = cmd.split("read-defects=")[1].strip('" ')
            seen.append(Path(path).read_text(encoding="utf-8"))
            return rl.Ran(7, "", "boom-detail")
        return real_run(cmd, cwd, timeout)

    monkeypatch.setattr(rl, "_run", spy)
    failing_review = f'"{PY}" -c "print(\'FAILED tests/t.py::a\'); raise SystemExit(1)"'
    rl.run(_args(review_cmd=failing_review,
                 fix_cmd='cmd read-defects={defects_file}',
                 max_iters=3, patience=3))
    # 第二次以後的 fix 應該看得到上一輪的失敗註記
    assert any("上一輪修復器失敗" in s for s in seen[1:]), seen


def test_successful_fixer_clears_previous_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    out = rl.run(_args(review_cmd=OK, fix_cmd=OK, max_iters=1))
    assert out["fixer_failures"] == 0
    assert "fixer_last_error" not in out


# ── 保證 3：地端先試修分層 ──────────────────────────────────────────────────
def test_local_tier_used_first_then_escalates_to_cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    calls: list[str] = []
    real_run = rl._run

    def spy(cmd, cwd, timeout):
        if cmd.startswith("LOCAL") or cmd.startswith("CLOUD"):
            calls.append(cmd.split()[0])
            return rl.Ran(0, "", "")
        return real_run(cmd, cwd, timeout)

    monkeypatch.setattr(rl, "_run", spy)
    failing_review = f'"{PY}" -c "print(\'FAILED tests/t.py::a\'); raise SystemExit(1)"'
    out = rl.run(_args(review_cmd=failing_review, fix_cmd="CLOUD fix",
                       local_fix_cmd="LOCAL fix", max_local_attempts=1,
                       max_iters=3, patience=3))
    # iteration 1 走地端，之後升級雲端
    assert calls[0] == "LOCAL"
    assert "CLOUD" in calls, calls
    assert out["fixer_tiers"][0] == "local"
    assert "cloud" in out["fixer_tiers"]


def test_no_local_cmd_means_all_cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    failing_review = f'"{PY}" -c "print(\'FAILED tests/t.py::a\'); raise SystemExit(1)"'
    out = rl.run(_args(review_cmd=failing_review, fix_cmd=OK, max_iters=2, patience=2))
    assert set(out["fixer_tiers"]) == {"cloud"}


def test_warns_when_local_attempts_swallow_all_iterations(capsys):
    """--max-local-attempts >= --max-iters 會讓雲端永遠輪不到，必須警告。"""
    rl.main(["--mode", "test", "--phase", "6", "--review-cmd", OK, "--fix-cmd", OK,
             "--local-fix-cmd", OK, "--max-local-attempts", "3", "--max-iters", "3",
             "--workdir", "."])
    assert "永遠不會被呼叫" in capsys.readouterr().err


def test_run_tolerates_namespace_without_new_flags(tmp_path, monkeypatch):
    """舊呼叫端（手搭 Namespace）不補新旗標也要能跑，沿用預設行為。"""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    args = Namespace(mode="test", phase="6", run_id="r", max_iters=1, patience=2,
                     max_tokens=None, workdir=None, review_cmd=OK, fix_cmd=OK,
                     reviewer_model=None, fixer_model=None, available=None)
    out = rl.run(args)
    assert out["status"] in {"resolved", "escalated"}
