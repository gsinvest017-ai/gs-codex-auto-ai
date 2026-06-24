"""Stage 1 — run_phase.py 事件橋接器測試。

驗證 phase 邊界事件確定性寫入 log/events.jsonl，且 run_id 跨「行程」持久化、
能被 progress.summarize() 正確解讀、能 resume。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_phase = _load("run_phase", "tools/run_phase.py")
progress = _load("progress_mod", "tools/progress.py")


def test_start_begin_end_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    rid = run_phase.cmd_start(tmp_path, None)
    assert rid.startswith("run-")
    assert (tmp_path / "log" / "current_run.txt").read_text(encoding="utf-8").strip() == rid

    run_phase.cmd_begin(tmp_path, "0", None)
    run_phase.cmd_end(tmp_path, "0", "success", None, None)
    run_phase.cmd_begin(tmp_path, "3", None)

    events_path = tmp_path / "log" / "events.jsonl"
    assert events_path.exists()
    summary = progress.summarize(progress.read_events(events_path))
    assert summary["current"] == 3          # 最近一個 phase_start 是 phase3
    assert 0 in summary["completed"]         # phase0 已 success
    assert summary["failed"] is False


def test_failure_status_sets_failed_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    run_phase.cmd_start(tmp_path, "run-fixed-1")
    run_phase.cmd_begin(tmp_path, "5", "run-fixed-1")
    run_phase.cmd_end(tmp_path, "5", "failure", "BuildError", "run-fixed-1")

    summary = progress.summarize(progress.read_events(tmp_path / "log" / "events.jsonl"))
    assert summary["failed"] is True


def test_run_id_persists_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    rid = run_phase.cmd_start(tmp_path, None)
    run_phase.cmd_begin(tmp_path, "0", None)        # 不傳 run_id → 應從 pointer 解析到同一個

    sys.path.insert(0, str(ROOT))
    from src.codexautoai_v2.state import RunState
    st = RunState.resume_or_new(str(tmp_path / "log" / "state.json"), rid)
    assert st.run_id == rid
    assert st.phase == "phase0"                      # set_phase 已持久化


def test_main_always_exits_zero_on_bad_args(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # 缺 --phase 會被 argparse 擋（SystemExit 2），但合法子命令的內部錯誤須 fail-open。
    rc = run_phase.main(["status"])
    assert rc == 0
