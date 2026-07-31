"""Stage 3 — run_build.py：plan（拓樸/循環拒絕）、gen-tests、guarded build。零 Codex。"""
import importlib.util
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_build", ROOT / "tools/run_build.py")
run_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_build)

PY = sys.executable


def _write_manifest(tmp_path, fns):
    m = tmp_path / "fn-manifest.json"
    m.write_text(json.dumps(fns), encoding="utf-8")
    return str(m)


def test_plan_orders_by_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    manifest = _write_manifest(tmp_path, [
        {"id": "A", "file": "src/a.py", "deps": []},
        {"id": "B", "file": "src/b.py", "deps": ["A"]},
    ])
    out = run_build.cmd_plan(Namespace(manifest=manifest, run_id=None))
    assert out["status"] == "planned"
    assert out["batch_count"] == 2          # a.py 先、b.py 後


def test_plan_rejects_dependency_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    manifest = _write_manifest(tmp_path, [
        {"id": "A", "file": "src/a.py", "deps": ["B"]},
        {"id": "B", "file": "src/b.py", "deps": ["A"]},
    ])
    out = run_build.cmd_plan(Namespace(manifest=manifest, run_id=None))
    assert out["status"] == "escalated"
    assert out["reason"] == "dependency_cycle"


def test_gen_tests_from_ears(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    spec_md = tmp_path / "spec.md"
    spec_md.write_text(
        "#### Scenario: FN-001-S1 — 加總\n"
        "- GIVEN 兩個整數\n- WHEN 呼叫 add\n- THEN 回傳和\n", encoding="utf-8")
    out_file = tmp_path / "test_props.py"
    out = run_build.cmd_gen_tests(Namespace(spec=str(spec_md), out=str(out_file)))
    assert out["status"] == "generated" and out["count"] == 1
    txt = out_file.read_text(encoding="utf-8")
    assert "def test_FN_001_S1" in txt and "assert False" in txt


def test_build_refuses_framework_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    manifest = _write_manifest(tmp_path, [{"id": "A", "file": "a.py", "deps": []}])
    out = run_build.cmd_build(Namespace(
        manifest=manifest, repo_root=str(run_build._TOOL_ROOT),
        build_cmd="true", run_id=None))
    assert out["status"] == "refused"


def test_build_worktree_merges_into_target_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    target = tmp_path / "proj"
    target.mkdir()
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=target, check=True, capture_output=True)
    (target / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=target, check=True, capture_output=True)

    manifest = _write_manifest(tmp_path, [{"id": "A", "file": "mod.py", "deps": []}])
    build_cmd = f'"{PY}" -c "open(\'mod.py\',\'w\').write(\'X=1\')"'
    out = run_build.cmd_build(Namespace(
        manifest=manifest, repo_root=str(target), build_cmd=build_cmd, run_id=None))
    assert out["status"] == "built"
    assert (target / "mod.py").exists()


# ── Phase 5 語法 gate（每批後的便宜 gate，避免錯誤累積到 Phase 6）──────────
def _gate_args(tmp_path, **kw):
    from argparse import Namespace
    base = dict(files=None, manifest=None, batch=None, run_id="run-gate")
    base.update(kw)
    return Namespace(**base)


def test_gate_passes_on_valid_python(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    f = tmp_path / "ok.py"
    f.write_text("def f():\n    return 1\n", encoding="utf-8")
    out = run_build.cmd_gate(_gate_args(tmp_path, files=["ok.py"]))
    assert out["status"] == "ok"
    assert out["checked"] == 1


def test_gate_flags_syntax_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "bad.py").write_text("def f(:\n    pass\n", encoding="utf-8")
    out = run_build.cmd_gate(_gate_args(tmp_path, files=["bad.py"]))
    assert out["status"] == "syntax_error"
    assert out["failures"][0]["file"] == "bad.py"


def test_gate_treats_missing_file_as_defect(tmp_path, monkeypatch):
    """Codex 沒把檔案寫出來（或寫到別的位置）本身就是缺陷。"""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    out = run_build.cmd_gate(_gate_args(tmp_path, files=["nope.py"]))
    assert out["status"] == "syntax_error"
    assert "不存在" in out["failures"][0]["message"]


def test_gate_no_files_is_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    out = run_build.cmd_gate(_gate_args(tmp_path, files=[]))
    assert out["status"] == "no_files"


def test_gate_reads_files_from_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def g(:\n", encoding="utf-8")
    man = tmp_path / "m.json"
    man.write_text(json.dumps([
        {"id": "FN-1", "file": "a.py", "deps": []},
        {"id": "FN-2", "file": "b.py", "deps": []},
    ]), encoding="utf-8")
    out = run_build.cmd_gate(_gate_args(tmp_path, manifest=str(man)))
    assert out["status"] == "syntax_error"
    assert [f["file"] for f in out["failures"]] == ["b.py"]


def test_gate_batch_out_of_range_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    man = tmp_path / "m.json"
    man.write_text(json.dumps([{"id": "FN-1", "file": "a.py", "deps": []}]), encoding="utf-8")
    out = run_build.cmd_gate(_gate_args(tmp_path, manifest=str(man), batch=99))
    assert out["status"] == "error"
    assert "超出範圍" in out["reason"]


def test_gate_does_not_emit_terminal_error_event(tmp_path, monkeypatch):
    """語法 gate 失敗是可修復的中間狀態，不能發 event_type=error（會被判成整條 run 升級）。"""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "bad.py").write_text("def f(:\n", encoding="utf-8")
    run_build.cmd_gate(_gate_args(tmp_path, files=["bad.py"]))
    events = (tmp_path / "log" / "events.jsonl").read_text(encoding="utf-8")
    kinds = [json.loads(line)["event_type"] for line in events.splitlines() if line.strip()]
    assert "error" not in kinds
    assert "tool_call" in kinds
