"""
test_state.py — Tests for src/codexautoai_v2/state.py

Covers:
  - mark_done / is_done
  - completed_actions ordering preserved
  - checkpoint → load round-trip
  - resume_or_new: matching run_id resumes; new run_id starts empty
  - exactly-once: record_side_effect / already_applied; survives reload
  - load raises StateError on malformed / tampered JSON
"""

import json
import pytest

from src.codexautoai_v2.state import RunState, StateError, SCHEMA_VERSION


# ─────────────────────────────────────────────────────────────── helpers ──────

def _write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


# ─────────────────────────────────────── mark_done / is_done / ordering ──────

def test_mark_done_and_is_done():
    state = RunState(run_id="run-001")
    assert not state.is_done("FN-001")
    state.mark_done("FN-001")
    assert state.is_done("FN-001")


def test_completed_order_preserved():
    state = RunState(run_id="run-002")
    ids = [f"FN-{i:03d}" for i in range(1, 8)]
    for action_id in ids:
        state.mark_done(action_id)
    assert state.completed_actions() == ids


def test_mark_done_idempotent():
    state = RunState(run_id="run-003")
    state.mark_done("FN-001")
    state.mark_done("FN-001")  # second call must not duplicate
    assert state.completed_actions().count("FN-001") == 1


def test_completed_actions_returns_copy():
    state = RunState(run_id="run-004")
    state.mark_done("FN-001")
    result = state.completed_actions()
    result.append("INTRUDER")
    assert "INTRUDER" not in state.completed_actions()


# ───────────────────────────────────────────── checkpoint → load round-trip ──

def test_checkpoint_and_load_roundtrip(tmp_path):
    cp = tmp_path / "state.json"
    state = RunState(run_id="run-rt-01", phase="build")
    state.mark_done("FN-001")
    state.mark_done("FN-002")
    state.record_side_effect("se-key-01")
    state.checkpoint(str(cp))

    loaded = RunState.load(str(cp))
    assert loaded.run_id == "run-rt-01"
    assert loaded.phase == "build"
    assert loaded.completed_actions() == ["FN-001", "FN-002"]
    assert loaded.already_applied("se-key-01")


def test_checkpoint_creates_parent_dirs(tmp_path):
    cp = tmp_path / "nested" / "deep" / "state.json"
    state = RunState(run_id="run-dir-01")
    state.checkpoint(str(cp))
    assert cp.exists()


def test_checkpoint_contains_schema_version(tmp_path):
    cp = tmp_path / "state.json"
    RunState(run_id="run-sv-01").checkpoint(str(cp))
    data = json.loads(cp.read_text())
    assert data["schema_version"] == SCHEMA_VERSION


# ───────────────────────────────────────────────────── resume_or_new logic ──

def test_resume_or_new_matching_run_id_resumes(tmp_path):
    """FN-001..006 done → resume at same run_id → still done; continue at FN-007."""
    cp = tmp_path / "state.json"
    run_id = "run-resume-01"

    initial = RunState(run_id=run_id, phase="build")
    for i in range(1, 7):
        initial.mark_done(f"FN-{i:03d}")
    initial.checkpoint(str(cp))

    resumed = RunState.resume_or_new(str(cp), run_id=run_id)
    for i in range(1, 7):
        assert resumed.is_done(f"FN-{i:03d}"), f"FN-{i:03d} should still be done after resume"
    assert not resumed.is_done("FN-007"), "FN-007 should NOT be done yet"

    # continue from FN-007
    resumed.mark_done("FN-007")
    assert resumed.is_done("FN-007")


def test_resume_or_new_different_run_id_starts_empty(tmp_path):
    cp = tmp_path / "state.json"
    initial = RunState(run_id="run-A")
    initial.mark_done("FN-001")
    initial.checkpoint(str(cp))

    fresh = RunState.resume_or_new(str(cp), run_id="run-B")
    assert fresh.run_id == "run-B"
    assert fresh.completed_actions() == []
    assert not fresh.is_done("FN-001")


def test_resume_or_new_no_checkpoint_starts_empty(tmp_path):
    cp = tmp_path / "nonexistent.json"
    state = RunState.resume_or_new(str(cp), run_id="run-new-01")
    assert state.run_id == "run-new-01"
    assert state.completed_actions() == []


# ─────────────────────────────────────────────────── exactly-once / STATE-R3 ──

def test_record_side_effect_and_already_applied():
    state = RunState(run_id="run-se-01")
    assert not state.already_applied("pay-txn-42")
    state.record_side_effect("pay-txn-42")
    assert state.already_applied("pay-txn-42")


def test_side_effect_persists_across_load(tmp_path):
    cp = tmp_path / "state.json"
    state = RunState(run_id="run-se-persist-01")
    state.record_side_effect("email-sent-001")
    state.checkpoint(str(cp))

    loaded = RunState.load(str(cp))
    assert loaded.already_applied("email-sent-001"), \
        "Side effect must survive checkpoint→load to prevent double-apply"


def test_side_effect_idempotent():
    state = RunState(run_id="run-se-02")
    state.record_side_effect("key-abc")
    state.record_side_effect("key-abc")  # second call must not duplicate
    assert state.already_applied("key-abc")
    assert state._side_effects.count("key-abc") == 1


# ─────────────────────────────────────────────────── load error paths (SECGOV-R8) ──

def test_load_missing_file_raises(tmp_path):
    with pytest.raises(StateError, match="not found"):
        RunState.load(str(tmp_path / "ghost.json"))


def test_load_not_json_raises(tmp_path):
    cp = tmp_path / "bad.json"
    cp.write_text("this is not json", encoding="utf-8")
    with pytest.raises(StateError):
        RunState.load(str(cp))


def test_load_missing_schema_version_raises(tmp_path):
    cp = tmp_path / "state.json"
    _write_json(cp, {
        "run_id": "r1", "phase": "init",
        "completed_actions": [], "side_effects": []
        # schema_version intentionally omitted
    })
    with pytest.raises(StateError):
        RunState.load(str(cp))


def test_load_wrong_schema_version_raises(tmp_path):
    cp = tmp_path / "state.json"
    _write_json(cp, {
        "schema_version": 999,
        "run_id": "r1", "phase": "init",
        "completed_actions": [], "side_effects": []
    })
    with pytest.raises(StateError, match="schema_version"):
        RunState.load(str(cp))


def test_load_missing_run_id_raises(tmp_path):
    cp = tmp_path / "state.json"
    _write_json(cp, {
        "schema_version": SCHEMA_VERSION,
        "phase": "init",
        "completed_actions": [], "side_effects": []
    })
    with pytest.raises(StateError):
        RunState.load(str(cp))


def test_load_missing_completed_actions_raises(tmp_path):
    cp = tmp_path / "state.json"
    _write_json(cp, {
        "schema_version": SCHEMA_VERSION,
        "run_id": "r1", "phase": "init",
        "side_effects": []
        # completed_actions omitted
    })
    with pytest.raises(StateError):
        RunState.load(str(cp))


def test_load_missing_side_effects_raises(tmp_path):
    cp = tmp_path / "state.json"
    _write_json(cp, {
        "schema_version": SCHEMA_VERSION,
        "run_id": "r1", "phase": "init",
        "completed_actions": []
        # side_effects omitted
    })
    with pytest.raises(StateError):
        RunState.load(str(cp))


def test_load_empty_run_id_raises(tmp_path):
    cp = tmp_path / "state.json"
    _write_json(cp, {
        "schema_version": SCHEMA_VERSION,
        "run_id": "",  # empty string — invalid
        "phase": "init",
        "completed_actions": [], "side_effects": []
    })
    with pytest.raises(StateError):
        RunState.load(str(cp))


def test_load_non_list_completed_actions_raises(tmp_path):
    cp = tmp_path / "state.json"
    _write_json(cp, {
        "schema_version": SCHEMA_VERSION,
        "run_id": "r1", "phase": "init",
        "completed_actions": "FN-001",  # wrong type
        "side_effects": []
    })
    with pytest.raises(StateError):
        RunState.load(str(cp))


def test_load_top_level_not_object_raises(tmp_path):
    cp = tmp_path / "state.json"
    cp.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(StateError):
        RunState.load(str(cp))


# ──────────────────────────────────────────────── set_phase persistence ──────

def test_set_phase_persists(tmp_path):
    cp = tmp_path / "state.json"
    state = RunState(run_id="run-phase-01", phase="init")
    state.set_phase("deploy")
    state.checkpoint(str(cp))
    loaded = RunState.load(str(cp))
    assert loaded.phase == "deploy"
