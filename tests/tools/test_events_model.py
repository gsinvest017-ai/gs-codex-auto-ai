"""events_model.py — 共用事件讀取層測試（desktop / extension / progress 三邊共用）。"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    """載入 tools/ 下的腳本，並註冊進 sys.modules。

    註冊是必要的：不註冊時 `dataclasses` / `typing` 等要靠
    `sys.modules[cls.__module__]` 解析註解的機制會炸。
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


em = _load("events_model", "tools/events_model.py")


def _write(path: Path, events: list[dict], trailing: str = "") -> Path:
    text = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    path.write_text(text + "\n" + trailing, encoding="utf-8")
    return path


# ── phase 欄位正規化 ──────────────────────────────────────────────────────────
def test_phase_num_accepts_prefixed_int_and_str():
    assert em.phase_num("phase3") == 3
    assert em.phase_num(3) == 3
    assert em.phase_num("3") == 3


def test_phase_num_rejects_junk_and_bool():
    assert em.phase_num(None) is None
    assert em.phase_num("phaseX") is None
    # bool 是 int 的子型別，不能被當成 phase 編號
    assert em.phase_num(True) is None


# ── 容錯讀檔 ────────────────────────────────────────────────────────────────
def test_missing_file_is_not_started(tmp_path):
    model = em.load_model(tmp_path / "nope.jsonl")
    assert model["log_exists"] is False
    assert model["state"] == em.STATE_NOT_STARTED
    assert model["event_count"] == 0


def test_tolerates_half_written_last_line(tmp_path):
    """pipeline 正在 append 時最後一行可能不完整——不能整份讀失敗。"""
    p = _write(tmp_path / "e.jsonl",
               [{"event_type": "phase_start", "phase": "phase2"}],
               trailing='{"event_type": "pha')
    model = em.load_model(p)
    assert model["event_count"] == 1
    assert model["marker"] == 2


def test_non_dict_lines_are_skipped(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text('"just a string"\n42\n{"event_type":"phase_start","phase":"phase1"}\n',
                 encoding="utf-8")
    model = em.load_model(p)
    assert model["event_count"] == 1


# ── 狀態推導 ────────────────────────────────────────────────────────────────
def test_running_marks_current_phase_active(tmp_path):
    p = _write(tmp_path / "e.jsonl", [
        {"event_type": "phase_start", "phase": "phase0"},
        {"event_type": "phase_end", "phase": "phase0", "status": "success"},
        {"event_type": "phase_start", "phase": "phase5"},
    ])
    model = em.load_model(p)
    assert model["state"] == em.STATE_RUNNING
    assert model["completed"] == [0]
    states = {ph["num"]: ph["state"] for ph in model["phases"]}
    assert states[0] == "done"
    assert states[5] == "active"
    assert states[6] == "pending"


def test_error_event_escalates_and_is_collected(tmp_path):
    p = _write(tmp_path / "e.jsonl", [
        {"event_type": "phase_start", "phase": "phase6"},
        {"event_type": "error", "phase": "phase6", "reason": "no_progress",
         "status": "escalated", "timestamp": "2026-07-31T00:00:00+00:00"},
    ])
    model = em.load_model(p)
    assert model["state"] == em.STATE_ESCALATED
    assert model["failed"] is True
    assert model["errors"][-1]["reason"] == "no_progress"
    states = {ph["num"]: ph["state"] for ph in model["phases"]}
    assert states[6] == "failed"


def test_phase7_success_is_done(tmp_path):
    p = _write(tmp_path / "e.jsonl", [
        {"event_type": "phase_start", "phase": "phase7"},
        {"event_type": "phase_end", "phase": "phase7", "status": "success"},
    ])
    model = em.load_model(p)
    assert model["state"] == em.STATE_DONE


def test_marker_survives_missed_phase_start(tmp_path):
    """漏接 phase_start 時，進度不應卡住（沿用 progress.py 的既有行為）。"""
    p = _write(tmp_path / "e.jsonl", [
        {"event_type": "phase_end", "phase": "phase4", "status": "success"},
    ])
    assert em.load_model(p)["marker"] == 4


def test_iteration_and_cost_come_from_loop_ticks(tmp_path):
    p = _write(tmp_path / "e.jsonl", [
        {"event_type": "phase_start", "phase": "phase6"},
        {"event_type": "loop_tick", "phase": "phase6", "iteration": 1,
         "cumulative_cost_usd": 0.1},
        {"event_type": "loop_tick", "phase": "phase6", "iteration": 3,
         "cumulative_cost_usd": 0.75},
    ])
    model = em.load_model(p)
    assert model["iteration"] == 3
    assert model["cost_usd"] == 0.75


# ── JSON 可序列化（extension 走 child_process 讀 stdout）────────────────────
def test_model_is_json_serialisable(tmp_path):
    p = _write(tmp_path / "e.jsonl", [{"event_type": "phase_start", "phase": "phase1"}])
    json.dumps(em.load_model(p))          # completed 是 set 會在這裡爆


# ── 渲染器共用（progress.py 與面板不得漂移）─────────────────────────────────
def test_render_lines_and_summary_lines_agree(tmp_path):
    p = _write(tmp_path / "e.jsonl", [
        {"event_type": "phase_start", "phase": "phase0"},
        {"event_type": "phase_end", "phase": "phase0", "status": "success"},
        {"event_type": "phase_start", "phase": "phase3"},
    ])
    events = em.read_events(p)
    from_model = em.render_lines(em.build_model(events, log_exists=True))
    from_summary = em.render_summary_lines(em.summarize(events), log_exists=True)
    assert from_model[0] == from_summary[0]
    assert "Phase 3/7" in from_model[0]


def test_render_not_started_when_log_absent():
    lines = em.render_summary_lines(em.summarize([]), log_exists=False)
    assert "尚未開始" in lines[0]


# ── run 邊界：跨 run 不得互相汙染 ─────────────────────────────────────────────
def test_run_start_resets_previous_run_state(tmp_path):
    """events.jsonl 是 append-only；上一輪的 escalation 不該顯示成本輪狀態。"""
    p = _write(tmp_path / "e.jsonl", [
        # 第一輪：跑到 phase6 然後升級失敗
        {"event_type": "run_start", "phase": "phase0", "run_id": "run-1"},
        {"event_type": "phase_start", "phase": "phase6"},
        {"event_type": "loop_tick", "phase": "phase6", "iteration": 3,
         "cumulative_cost_usd": 0.9},
        {"event_type": "error", "phase": "phase6", "reason": "no_progress"},
        # 第二輪：重新開始，只跑到 phase1
        {"event_type": "run_start", "phase": "phase0", "run_id": "run-2"},
        {"event_type": "phase_start", "phase": "phase1"},
    ])
    model = em.load_model(p)
    assert model["state"] == em.STATE_RUNNING     # 不該還是 escalated
    assert model["failed"] is False
    assert model["errors"] == []                 # 上一輪的錯誤不該被算進來
    assert model["marker"] == 1
    assert model["iteration"] == 0
    assert model["cost_usd"] == 0.0


def test_logs_without_run_start_behave_as_before(tmp_path):
    """舊的 events.jsonl 沒有 run_start，行為必須與過去一致（不重置）。"""
    p = _write(tmp_path / "e.jsonl", [
        {"event_type": "phase_start", "phase": "phase3"},
        {"event_type": "error", "phase": "phase3", "reason": "boom"},
    ])
    model = em.load_model(p)
    assert model["state"] == em.STATE_ESCALATED
    assert len(model["errors"]) == 1


# ── 只有 loop_tick 也要能定位 phase ────────────────────────────────────────
def test_phase_derived_from_loop_tick_without_phase_start(tmp_path):
    """run_loop.py 只發 loop_tick/tool_call/error，從不發 phase_start。

    只認 phase_start 的話，Phase 6 修復迴圈整輪都會顯示成 Phase 0。
    """
    p = _write(tmp_path / "e.jsonl", [
        {"event_type": "loop_tick", "phase": "phase6", "iteration": 1,
         "cumulative_cost_usd": 0.2},
    ])
    model = em.load_model(p)
    assert model["marker"] == 6
    assert model["current_name"] == "測試"


def test_tool_call_phase_also_counts(tmp_path):
    p = _write(tmp_path / "e.jsonl", [
        {"event_type": "tool_call", "phase": "phase5", "tool": "syntax_gate",
         "status": "failure"},
    ])
    assert em.load_model(p)["marker"] == 5


def test_phase_end_does_not_park_cursor_on_finished_phase(tmp_path):
    """phase_end 代表離開該 phase，不該把游標留在那；但要算進 completed。"""
    p = _write(tmp_path / "e.jsonl", [
        {"event_type": "phase_start", "phase": "phase2"},
        {"event_type": "phase_end", "phase": "phase2", "status": "success"},
    ])
    model = em.load_model(p)
    assert model["completed"] == [2]
    assert model["current"] == 2      # 最後一個非 phase_end 的 phase 事件
