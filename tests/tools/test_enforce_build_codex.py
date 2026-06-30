"""enforce_build_codex PreToolUse 守門員測試（純函式 evaluate，零 LLM）。

驗證：只在 phase5 進行中擋 Claude 對 src/ 的 Edit/Write/MultiEdit；
其餘 phase、build 已結束、非 src/、非守門工具、無 state、停用旗標 一律放行。
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


enf = _load("enforce_build_codex", "tools/enforce_build_codex.py")


def _write_state(root: Path, phase: str, completed=None):
    log = root / "log"
    log.mkdir(parents=True, exist_ok=True)
    (log / "state.json").write_text(json.dumps({
        "schema_version": 1, "run_id": "run-x", "phase": phase,
        "completed_actions": completed or [], "side_effects": [],
    }), encoding="utf-8")


def _payload(tool, root, rel="src/foo.py"):
    return {"tool_name": tool, "tool_input": {"file_path": str(root / rel)}}


@pytest.fixture(autouse=True)
def _clear_disable(monkeypatch):
    monkeypatch.delenv("CODEXAUTOAI_NO_BUILD_ENFORCE", raising=False)


def test_blocks_edit_src_during_build(tmp_path):
    _write_state(tmp_path, "phase5")
    assert enf.evaluate(_payload("Edit", tmp_path), tmp_path) is not None


def test_blocks_write_and_multiedit_src_during_build(tmp_path):
    _write_state(tmp_path, "phase5")
    assert enf.evaluate(_payload("Write", tmp_path), tmp_path) is not None
    assert enf.evaluate(_payload("MultiEdit", tmp_path), tmp_path) is not None


def test_allows_non_src_during_build(tmp_path):
    _write_state(tmp_path, "phase5")
    assert enf.evaluate(_payload("Write", tmp_path, "docs/spec.md"), tmp_path) is None
    assert enf.evaluate(_payload("Edit", tmp_path, "tests/test_x.py"), tmp_path) is None


def test_allows_after_build_ended(tmp_path):
    _write_state(tmp_path, "phase5", completed=["phase5-end"])
    assert enf.evaluate(_payload("Edit", tmp_path), tmp_path) is None


def test_allows_other_phase(tmp_path):
    _write_state(tmp_path, "phase3")
    assert enf.evaluate(_payload("Edit", tmp_path), tmp_path) is None


def test_allows_when_no_state(tmp_path):
    assert enf.evaluate(_payload("Edit", tmp_path), tmp_path) is None


def test_allows_non_guarded_tool(tmp_path):
    _write_state(tmp_path, "phase5")
    assert enf.evaluate({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}, tmp_path) is None


def test_disable_env_allows(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXAUTOAI_NO_BUILD_ENFORCE", "1")
    _write_state(tmp_path, "phase5")
    assert enf.evaluate(_payload("Edit", tmp_path), tmp_path) is None


def test_main_emits_deny_json(tmp_path, monkeypatch, capsys):
    _write_state(tmp_path, "phase5")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    payload = json.dumps(_payload("Edit", tmp_path))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    rc = enf.main()
    out = capsys.readouterr().out
    assert rc == 0
    obj = json.loads(out)
    assert obj["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_allows_silently(tmp_path, monkeypatch, capsys):
    _write_state(tmp_path, "phase3")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    payload = json.dumps(_payload("Edit", tmp_path))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    rc = enf.main()
    out = capsys.readouterr().out
    assert rc == 0 and out.strip() == ""
