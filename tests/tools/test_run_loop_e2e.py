"""Stage 2 — run_loop.py 端到端：用假 review/fix 指令證明守衛在無 Codex 下會 fire。

對映 tests/codexautoai_v2/test_orchestrator.py 的 resolved / no-progress escalate，
但走真實 subprocess + 解析路徑。零 Codex / 零網路。
"""
import importlib.util
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_loop", ROOT / "tools/run_loop.py")
run_loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_loop)

PY = sys.executable


def _args(**kw):
    base = dict(mode="test", phase="6", run_id="run-test-1", max_iters=3, patience=2,
                max_tokens=None, workdir=None, review_cmd="", fix_cmd="",
                compile_cmd=None, fix_retries=1,
                reviewer_model=None, fixer_model=None, available=None)
    base.update(kw)
    return Namespace(**base)


def test_converges_to_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # review：done.txt 不存在就回報一個失敗(exit 1)，存在則 0 個失敗(exit 0)
    review = tmp_path / "review.py"
    review.write_text(
        "import os,sys\n"
        "open('done.txt').close() if os.path.exists('done.txt') else None\n"
        "sys.exit(0) if os.path.exists('done.txt') else (print('FAILED tests/t.py::a') or sys.exit(1))\n",
        encoding="utf-8")
    # fix：建立 done.txt（模擬 codex 修好）
    fix = tmp_path / "fix.py"
    fix.write_text("open('done.txt','w').close()\n", encoding="utf-8")

    out = run_loop.run(_args(
        workdir=str(tmp_path),
        review_cmd=f'"{PY}" "{review}"',
        fix_cmd=f'"{PY}" "{fix}"'))
    assert out["status"] in ("resolved", "resolved_after_replan")
    # 事件有寫進 events.jsonl
    assert (tmp_path / "log" / "events.jsonl").exists()


def test_no_progress_escalates(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # review：永遠回報同樣的 2 個失敗；fix：什麼都不做 → 缺陷集不縮小
    review = tmp_path / "review.py"
    review.write_text(
        "import sys\nprint('FAILED tests/t.py::a')\nprint('FAILED tests/t.py::b')\nsys.exit(1)\n",
        encoding="utf-8")
    fix = tmp_path / "fix.py"
    fix.write_text("pass\n", encoding="utf-8")

    out = run_loop.run(_args(
        workdir=str(tmp_path),
        review_cmd=f'"{PY}" "{review}"',
        fix_cmd=f'"{PY}" "{fix}"'))
    assert out["status"] == "escalated"
    # 守衛觸發原因為無進度或達迭代上限
    assert out["reason"] in ("no_progress", "max_iterations", "budget")


def test_main_exit_code_reflects_status(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    review = tmp_path / "review.py"
    review.write_text("import sys\nprint('FAILED x::y')\nsys.exit(1)\n", encoding="utf-8")
    fix = tmp_path / "fix.py"
    fix.write_text("pass\n", encoding="utf-8")
    rc = run_loop.main(["--mode", "test", "--phase", "6", "--workdir", str(tmp_path),
                        "--review-cmd", f'"{PY}" "{review}"',
                        "--fix-cmd", f'"{PY}" "{fix}"'])
    assert rc == 3   # escalated
