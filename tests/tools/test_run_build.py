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
