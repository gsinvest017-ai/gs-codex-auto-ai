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


def test_warns_when_local_attempts_swallow_all_iterations(capsys, tmp_path, monkeypatch):
    """--max-local-attempts >= --max-iters 會讓雲端永遠輪不到，必須警告。

    **必須設 CLAUDE_PROJECT_DIR 並把 workdir 指到 tmp_path**：`main()` 會跑完整迴圈並
    寫 `log/events.jsonl` 與 `log/state.json`。不隔離的話 `_project_dir()` 會 fallback
    到 repo 根，把假事件寫進真的 `log/`——結果是進度視圖顯示一個不存在的 run，
    而且 `enforce_build_codex` 這個 PreToolUse hook 會誤以為正在 build 而擋下對
    src/tests/docs 的編輯。
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    rl.main(["--mode", "test", "--phase", "6", "--review-cmd", OK, "--fix-cmd", OK,
             "--local-fix-cmd", OK, "--max-local-attempts", "3", "--max-iters", "3",
             "--workdir", str(tmp_path)])
    assert "永遠不會被呼叫" in capsys.readouterr().err
    assert (tmp_path / "log" / "events.jsonl").exists()   # 確認真的寫在 tmp 而非 repo


def test_run_tolerates_namespace_without_new_flags(tmp_path, monkeypatch):
    """舊呼叫端（手搭 Namespace）不補新旗標也要能跑，沿用預設行為。"""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    args = Namespace(mode="test", phase="6", run_id="r", max_iters=1, patience=2,
                     max_tokens=None, workdir=None, review_cmd=OK, fix_cmd=OK,
                     reviewer_model=None, fixer_model=None, available=None)
    out = rl.run(args)
    assert out["status"] in {"resolved", "escalated"}


# ── 工具層失敗 ≠ 語意結論 ────────────────────────────────────────────────────
class TestToolFailureIsNotAVerdict:
    """實測跑出來的三個坑是同一類：run_loop 把「指令沒跑起來」當成「東西壞了 /
    東西是好的」。這會讓流水線在事實相反時給出相反結論——全綠的測試被判
    escalated/no_progress，或 reviewer 沒產出被當成沒缺陷（假通過）。
    """

    def test_posix_command_not_found(self):
        assert rl.Ran(127, "", "sh: pytest: command not found").launch_failed

    def test_windows_not_recognized(self):
        r = rl.Ran(1, "", "'C:/x/.venv/Scripts/python' is not recognized as an "
                          "internal or external command,")
        assert r.launch_failed, "正斜線 venv 路徑打不開，就是這個訊息"

    def test_windows_cannot_find_path(self):
        assert rl.Ran(3, "", "The system cannot find the path specified.").launch_failed

    def test_real_test_failure_is_not_a_launch_failure(self):
        """測試真的失敗時不能誤判成工具層失敗——那會讓真缺陷被當成環境問題。"""
        r = rl.Ran(1, "FAILED tests/test_a.py::test_x - assert 1 == 2", "")
        assert not r.launch_failed

    def test_timeout_is_not_a_launch_failure(self):
        """逾時代表它跑起來了只是太久，已經有 review:timeout 在處理。"""
        assert not rl.Ran(-1, "", "", timed_out=True).launch_failed

    def test_clean_run_is_not_a_launch_failure(self):
        assert not rl.Ran(0, "5 passed", "").launch_failed


def test_unlaunchable_test_command_is_reported_as_tool_failure(tmp_path, monkeypatch):
    """指令打不開時，reason 必須說是工具層問題，不能報成 no_progress。

    實測情境：skill 範例給的正斜線 venv 路徑，cmd.exe 認不得 → pytest 從沒跑過，
    但全綠的測試被判 escalated / no_progress，把人導向「測試修不動」的錯誤方向。
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    nonexistent = '"C:/nope/.venv/Scripts/python" -m pytest'
    out = rl.run(_args(review_cmd=nonexistent, fix_cmd=OK, max_iters=2, patience=2))
    assert out["status"] == "escalated"
    assert "tool_failed" in out["reason"], out["reason"]
    assert "no_progress" not in out["reason"], "工具層失敗被報成 no_progress"
    assert out.get("tool_failure"), "要留下原始錯誤讓人看得出是什麼指令打不開"
    assert any("tool:cannot-run-tests" in d for d in out["final_defects"])


def test_reviewer_producing_nothing_is_not_a_pass(tmp_path, monkeypatch):
    """reviewer 沒產出 {review_out} 時不可判 resolved——那是假通過。

    「說沒缺陷」與「根本沒產出」都是空的 {review_out}，但語意相反。
    實測 Phase 4 因此出現兩次假通過。
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    silent_fail = f'"{PY}" -c "raise SystemExit(1)"'
    out = rl.run(_args(mode="review", phase="4", review_cmd=silent_fail,
                       fix_cmd=OK, max_iters=1))
    assert out["status"] != "resolved", "reviewer 沒產出卻判通過"
    assert "tool_failed" in out["reason"], out["reason"]


def test_reviewer_saying_no_defects_still_resolves(tmp_path, monkeypatch):
    """相對照：reviewer 正常跑完、真的沒缺陷，就要收斂成 resolved。

    不驗這條的話，上面那條可以靠「一律不通過」作弊過關。
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    out = rl.run(_args(mode="review", phase="4", review_cmd=OK, fix_cmd=OK, max_iters=1))
    assert out["status"] == "resolved", out


def test_localised_shell_error_still_detected(tmp_path, monkeypatch):
    """cmd.exe 的錯誤訊息會**在地化**（實測中文 Windows 給「系統找不到指定的路徑」），
    rc 也只是 1——跟「測試失敗」外觀完全一樣。

    所以判斷不能只比對英文訊息；要看 pytest 自己的招牌字串有沒有出現。
    第一版就是只比英文，在這台機器上完全失效。
    """
    r = rl.Ran(1, "", "系統找不到指定的路徑。")
    assert not r.launch_failed, "英文樣式本來就對不上在地化訊息"
    assert not r.ran_pytest(), "沒有任何 pytest 痕跡 → 它根本沒跑"


def test_real_failure_output_counts_as_having_run(self=None):
    """測試真的跑了而且失敗——不能被當成工具層失敗，否則真缺陷會被當環境問題。"""
    r = rl.Ran(1, "collected 3 items\nFAILED tests/t.py::a - assert 1 == 2", "")
    assert r.ran_pytest()


# ── shell=True + 正斜線路徑（流水線回報的框架 bug #6）────────────────────────
import os          # noqa: E402
import subprocess  # noqa: E402

import pytest      # noqa: E402


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe 才有正斜線問題")
def test_forward_slash_exe_actually_runs_under_cmd(tmp_path):
    """對照組：同一支腳本，正斜線呼叫在 cmd.exe 下會失敗，換過反斜線才跑得起來。"""
    d = tmp_path / "bin"
    d.mkdir()
    (d / "hello.bat").write_text("@echo off\r\necho RAN_OK\r\n", encoding="utf-8")
    rel = "bin/hello.bat"

    before = subprocess.run(rel, shell=True, cwd=str(tmp_path),
                            capture_output=True, text=True, timeout=60)
    after = subprocess.run(rl.win_shell_cmd(rel), shell=True, cwd=str(tmp_path),
                           capture_output=True, text=True, timeout=60)

    assert "RAN_OK" not in (before.stdout or ""), (
        "對照組沒壞——若 cmd.exe 本來就吃正斜線，這個修正就不必要了")
    assert "RAN_OK" in (after.stdout or ""), f"換過反斜線就該跑得起來：{after!r}"


@pytest.mark.skipif(os.name != "nt", reason="只在 Windows 改寫")
def test_win_shell_cmd_only_touches_the_executable():
    """參數裡的正斜線可能是旗標或要傳給程式的路徑，不能一起改。"""
    got = rl.win_shell_cmd(".venv/Scripts/python -m pytest tests/tools -q")
    assert got == r".venv\Scripts\python -m pytest tests/tools -q"


@pytest.mark.skipif(os.name != "nt", reason="只在 Windows 改寫")
def test_win_shell_cmd_leaves_bare_commands_and_urls_alone():
    assert rl.win_shell_cmd("pytest -q") == "pytest -q"
    assert rl.win_shell_cmd("curl https://x/y -o a") == "curl https://x/y -o a"
    assert rl.win_shell_cmd("") == ""


def test_transient_tool_failure_does_not_poison_a_resolved_run(tmp_path, monkeypatch):
    """第一輪指令沒跑起來、後面恢復並收斂成功 → 結論不該還說是工具層失敗。

    `tool_fail` box 以前整輪只寫不清，於是一次短暫失敗（檔案鎖、防毒干擾）就會把
    最終 reason 蓋成 tool_failed——一個真的修好了的 run 宣稱自己是因為指令打不開
    而失敗，正是 M1 想解決的混淆的反面。
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    flag = tmp_path / "first_round_done.txt"
    # 第一輪：假裝指令打不開（模擬檔案鎖）。第二輪起：正常跑完、沒有缺陷。
    script = (
        "import pathlib,sys;"
        f"f=pathlib.Path(r'{flag}');"
        "first=not f.exists();"
        "f.write_text('x');"
        "sys.stderr.write('The system cannot find the path specified') "
        "if first else sys.stdout.write('2 passed');"
        "sys.exit(1 if first else 0)"
    )
    out = rl.run(_args(review_cmd=f'"{PY}" -c "{script}"', fix_cmd=OK,
                       max_iters=3, patience=3))

    assert out["status"] == "resolved", f"第二輪就該收斂：{out}"
    assert "tool_failed" not in (out["reason"] or ""), (
        f"收斂成功的 run 不該宣稱自己是工具層失敗：{out['reason']}")
    assert not out.get("tool_failure"), (
        f"上一輪的工具錯誤不該留在最終輸出：{out.get('tool_failure')}")


class TestFinalReason:
    """收尾覆寫 reason 的那道判斷——它被上游的逐輪清空遮住，只能直接驗。"""

    def test_tool_failure_wins_when_the_run_did_not_converge(self):
        got = rl.final_reason("no_progress", "pytest 打不開", "escalated")
        assert "tool_failed" in got and "pytest 打不開" in got

    def test_resolved_run_keeps_its_own_reason(self):
        """已經修好的 run 不該宣稱自己是工具層失敗——M1 那個混淆的反面。"""
        assert rl.final_reason("ok", "pytest 打不開", "resolved") == "ok"
        assert rl.final_reason(None, "pytest 打不開", "resolved") is None

    def test_no_tool_failure_leaves_reason_untouched(self):
        assert rl.final_reason("no_progress", "", "escalated") == "no_progress"
