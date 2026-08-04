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


# ── App 心跳武裝 ─────────────────────────────────────────────────────────────
def _write_app_run(root: Path, age_seconds: float = 0.0):
    import time
    log = root / "log"
    log.mkdir(parents=True, exist_ok=True)
    (log / "app-run.json").write_text(json.dumps({
        "prompt": "做一個記帳 CLI", "started_at": time.time() - age_seconds,
        "updated_at": time.time() - age_seconds, "pid": 1234,
    }), encoding="utf-8")


class TestAppRunArming:
    """守門員原本只認 `log/state.json`，而那是靠 Claude 自己去跑
    `run_phase.py begin` 寫出來的——它沒跑，hook 就 fail-open 放行，等於
    「Claude 不直接寫程式碼」這條核心不變式最後還是靠 Claude 自律。

    改由**桌面 App** 在啟動任務時寫下心跳標記，跟 LLM 有沒有照做無關。
    """

    def test_blocks_src_write_with_only_the_app_marker(self, tmp_path):
        """沒有 state.json（Claude 還沒自首）也要擋——這就是修的那個洞。"""
        _write_app_run(tmp_path)
        assert enf.evaluate(_payload("Write", tmp_path), tmp_path) is not None

    def test_blocks_tests_dir_too(self, tmp_path):
        _write_app_run(tmp_path)
        assert enf.evaluate(_payload("Write", tmp_path, "tests/t.py"), tmp_path) is not None

    def test_whitelist_still_passes(self, tmp_path):
        """Phase 2 的規劃產物是 Claude 的職責，武裝了也不能擋。"""
        _write_app_run(tmp_path)
        assert enf.evaluate(
            _payload("Write", tmp_path, "docs/requirements-spec.md"), tmp_path) is None

    def test_non_guarded_dir_still_passes(self, tmp_path):
        _write_app_run(tmp_path)
        assert enf.evaluate(_payload("Write", tmp_path, "log/note.md"), tmp_path) is None

    def test_stale_marker_does_not_arm(self, tmp_path):
        """App 關掉之後標記要自然過期，否則那個資料夾會被永久鎖住。"""
        _write_app_run(tmp_path, age_seconds=enf._APP_RUN_TTL + 60)
        assert enf.evaluate(_payload("Write", tmp_path), tmp_path) is None

    def test_no_marker_at_all_still_fails_open(self, tmp_path):
        """框架自身開發 / 手動使用不受影響——沒有 App 標記就不武裝。"""
        assert enf.evaluate(_payload("Write", tmp_path), tmp_path) is None

    def test_disable_flag_still_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEXAUTOAI_NO_BUILD_ENFORCE", "1")
        _write_app_run(tmp_path)
        assert enf.evaluate(_payload("Write", tmp_path), tmp_path) is None

    def test_ask_user_question_blocked_by_app_marker(self, tmp_path):
        """非停原則同理：App 說任務在跑，就不准把選擇丟回使用者。"""
        assert enf.evaluate({"tool_name": "AskUserQuestion"}, tmp_path) is None
        _write_app_run(tmp_path)
        assert enf.evaluate({"tool_name": "AskUserQuestion"}, tmp_path) is not None
