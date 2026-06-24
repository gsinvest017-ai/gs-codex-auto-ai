"""Stage 4 — compile-skip、fix-retries、run_phase resume、repo_context。零 Codex。"""
import importlib.util
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


run_loop = _load("run_loop", "tools/run_loop.py")
run_phase = _load("run_phase", "tools/run_phase.py")
repo_context = _load("repo_context", "tools/repo_context.py")
PY = sys.executable


def _args(**kw):
    base = dict(mode="test", phase="6", run_id="r1", max_iters=3, patience=2,
                max_tokens=None, workdir=None, review_cmd="", fix_cmd="",
                compile_cmd=None, fix_retries=1, reviewer_model=None,
                fixer_model=None, available=None)
    base.update(kw)
    return Namespace(**base)


def test_compile_failure_skips_reviewer_and_escalates(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # compile 永遠失敗 → review-cmd 不該被叫到（用會建立檔案的 review 證明它沒跑）
    review = tmp_path / "rev.py"
    review.write_text("open('REVIEW_RAN','w').close()\nprint('FAILED x::y')\n", encoding="utf-8")
    out = run_loop.run(_args(
        workdir=str(tmp_path),
        compile_cmd=f'"{PY}" -c "import sys; sys.exit(1)"',   # 編譯失敗
        review_cmd=f'"{PY}" "{review}"',
        fix_cmd=f'"{PY}" -c "pass"'))
    assert out["status"] == "escalated"                 # compile:failed 永不消失 → 守衛 fire
    assert not (tmp_path / "REVIEW_RAN").exists()         # reviewer 被跳過（省成本）


def test_fix_retries_invokes_fix_multiple_times(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    counter = tmp_path / "count.txt"
    review = tmp_path / "rev.py"
    review.write_text("import sys; print('FAILED x::y'); sys.exit(1)\n", encoding="utf-8")
    # fix 每次把 count +1 並回非零 → 應被重試 fix_retries 次/輪
    fix = tmp_path / "fix.py"
    fix.write_text(
        f"import sys; p=r'{counter}';"
        "open(p,'a').write('x'); sys.exit(1)\n", encoding="utf-8")
    run_loop.run(_args(
        workdir=str(tmp_path), max_iters=2, patience=5,
        review_cmd=f'"{PY}" "{review}"', fix_cmd=f'"{PY}" "{fix}"', fix_retries=3))
    # iter0 produce_fix 跳過；iter1 produce_fix 跑 → 3 次重試 = 3 個 x（至少）
    assert counter.exists() and len(counter.read_text()) >= 3


def test_run_phase_resume_reports_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    run_phase.cmd_start(tmp_path, "run-resume-1")
    run_phase.cmd_begin(tmp_path, "3", "run-resume-1")    # 進到 phase3 沒結束
    info = run_phase.cmd_resume(tmp_path)
    assert info["resumable"] is True
    assert info["run_id"] == "run-resume-1"
    assert info["phase"] == "phase3"


def test_repo_context_outputs_symbol_map(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def add(x, y):\n    return x + y\n\nclass Foo:\n    pass\n", encoding="utf-8")
    out = repo_context.build([str(f)], max_chars=2000)
    assert "add" in out or "Foo" in out          # 有抽到 symbol
    assert len(out) <= 2000
