"""enforce_build_codex PreToolUse 守門員測試（純函式 evaluate，零 LLM）。

驗證 Codex-first 硬分工（見 tools/enforce_build_codex.py 的 _ENFORCED_PHASES）：
Phase 3–7 進行中，擋 Claude 對 src/ tests/ docs/ 的 Edit/Write/MultiEdit；
Phase 0–2、build 已結束、非守門目錄、非守門工具、無 state、白名單、停用旗標 一律放行。
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


def test_blocks_tests_and_docs_during_build(tmp_path):
    """Codex-first 硬分工：tests/ 與 docs/ 的內容產出也一律走 Codex（不只 src/）。"""
    _write_state(tmp_path, "phase5")
    assert enf.evaluate(_payload("Write", tmp_path, "docs/spec.md"), tmp_path) is not None
    assert enf.evaluate(_payload("Edit", tmp_path, "tests/test_x.py"), tmp_path) is not None


def test_allows_non_guarded_dirs_during_build(tmp_path):
    """守門只針對 src/ tests/ docs/；其餘路徑（README、log、設定）不擋。"""
    _write_state(tmp_path, "phase5")
    assert enf.evaluate(_payload("Write", tmp_path, "README.md"), tmp_path) is None
    assert enf.evaluate(_payload("Edit", tmp_path, "log/notes.md"), tmp_path) is None


def test_whitelisted_planning_output_is_allowed(tmp_path):
    """Phase 2 的規劃產物 requirements-spec.md 屬 Claude 職責，即使在 docs/ 下也放行。"""
    _write_state(tmp_path, "phase5")
    assert enf.evaluate(
        _payload("Write", tmp_path, "docs/requirements-spec.md"), tmp_path) is None


def test_all_enforced_phases_block(tmp_path):
    """Phase 3–7 全部enforced；漏掉任何一個就等於分工在該階段失效。"""
    for phase in ("phase3", "phase4", "phase5", "phase6", "phase7"):
        _write_state(tmp_path, phase)
        assert enf.evaluate(_payload("Edit", tmp_path), tmp_path) is not None, phase


def test_planning_phases_do_not_block(tmp_path):
    """Phase 0–2 是規劃期，Claude 本來就該能寫檔。"""
    for phase in ("phase0", "phase1", "phase2"):
        _write_state(tmp_path, phase)
        assert enf.evaluate(_payload("Edit", tmp_path), tmp_path) is None, phase


def test_allows_after_build_ended(tmp_path):
    _write_state(tmp_path, "phase5", completed=["phase5-end"])
    assert enf.evaluate(_payload("Edit", tmp_path), tmp_path) is None


def test_allows_other_phase(tmp_path):
    _write_state(tmp_path, "phase2")   # phase3 起才 enforced
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
    _write_state(tmp_path, "phase2")   # phase3 起才 enforced
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    payload = json.dumps(_payload("Edit", tmp_path))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    rc = enf.main()
    out = capsys.readouterr().out
    assert rc == 0 and out.strip() == ""
