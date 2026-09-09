import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from native_usage import collect_native_children

START = 1788927900.0  # 2026-09-09 04:25 UTC


def write(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in events), encoding="utf-8")
    return path


def event(kind, **kw):
    return {"type": "event_msg", "timestamp": "2026-09-09T04:25:30Z", "payload": {"type": kind, **kw}}


def identity(tid, parent, stamp="2026-09-09T04:25:26Z"):
    return {"type": "session_meta", "payload": {"id": tid, "timestamp": stamp,
        "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent, "agent_path": "/root/worker"}}}}}


def usage(n):
    return event("token_count", info={"total_token_usage": {"input_tokens": n, "output_tokens": 2}})


def fixture(tmp_path):
    result = write(tmp_path / "cli.log", [{"type": "thread.started", "thread_id": "parent"}])
    sessions = tmp_path / "sessions"
    folder = sessions / "2026/09/09"
    return result, sessions, folder


def test_linked_child_and_grandchild_no_inherited_or_unrelated_usage(tmp_path):
    result, sessions, folder = fixture(tmp_path)
    own = lambda tid: event("thread_settings_applied", thread_id=tid, thread_settings={"model": "gpt-6-astra"})
    write(folder / "rollout-child.jsonl", [identity("child", "parent"), identity("parent", "unrelated"),
        usage(99999), event("task_complete"), own("child"), usage(10), usage(25), event("task_complete")])
    write(folder / "rollout-grand.jsonl", [identity("grand", "child"), own("grand"), usage(7), event("task_complete")])
    write(folder / "rollout-other.jsonl", [identity("other", "other-parent"), own("other"), usage(500)])
    records = collect_native_children(result, sessions, START, START + 100)
    assert [x["thread_id"] for x in records] == ["child", "grand"]
    assert [x["usage"]["input_tokens"] for x in records] == [25, 7]
    assert all(x["status"] == "ok" and x["actual_model"] == "gpt-6-astra" for x in records)
    assert records[0]["usage"]["cached_input_tokens"] is None


def test_old_future_and_duplicate_identity_are_not_counted(tmp_path):
    result, sessions, folder = fixture(tmp_path)
    for name, stamp in [("old", "2026-09-09T04:24:59Z"), ("future", "2026-09-09T04:27:00Z")]:
        write(folder / f"rollout-{name}.jsonl", [identity(name, "parent", stamp)])
    for name in ("a", "b"):
        write(folder / f"rollout-{name}.jsonl", [identity("child", "parent")])
    records = collect_native_children(result, sessions, START, START + 100)
    assert len(records) == 1
    assert records[0]["status"] == "unknown"
    assert all(v is None for v in records[0]["usage"].values())


def test_parent_history_cannot_supply_child_completion_model_or_tokens(tmp_path):
    result, sessions, folder = fixture(tmp_path)
    write(folder / "rollout-child.jsonl", [identity("child", "parent"),
        event("thread_settings_applied", thread_id="parent", thread_settings={"model": "wrong"}),
        usage(999), event("task_complete")])
    child = collect_native_children(result, sessions, START, START + 100)[0]
    assert child["actual_model"] is None
    assert child["status"] == "unknown"
    assert child["usage"]["input_tokens"] is None


def test_aborted_child_and_invalid_token_types(tmp_path):
    result, sessions, folder = fixture(tmp_path)
    write(folder / "rollout-child.jsonl", [identity("child", "parent"),
        event("thread_settings_applied", thread_id="child", thread_settings={}),
        event("token_count", info={"total_token_usage": {"input_tokens": -1, "output_tokens": True}}),
        event("turn_aborted")])
    child = collect_native_children(result, sessions, START, START + 100)[0]
    assert child["status"] == "failed"
    assert all(v is None for v in child["usage"].values())


def test_malformed_metadata_and_missing_thread_are_safe(tmp_path):
    result, sessions, folder = fixture(tmp_path)
    malformed = identity("child", "parent")
    malformed["payload"]["source"]["subagent"] = []
    write(folder / "rollout-malformed.jsonl", [malformed])
    assert collect_native_children(result, sessions, START, START + 100) == []
    assert collect_native_children(tmp_path / "missing", sessions, START, START + 100) == []


def test_later_resumed_child_cannot_change_original_run_metrics(tmp_path):
    result, sessions, folder = fixture(tmp_path)
    events = [identity("child", "parent"),
        event("thread_settings_applied", thread_id="child", thread_settings={"model": "original"}),
        usage(25), event("task_complete")]
    later = [event("task_started"), usage(900), event("turn_aborted"),
        event("thread_settings_applied", thread_id="child", thread_settings={"model": "later"})]
    for item in later:
        item["timestamp"] = "2026-09-09T05:00:00Z"
    write(folder / "rollout-child.jsonl", events + later)
    child = collect_native_children(result, sessions, START, START + 100)[0]
    assert child["usage"]["input_tokens"] == 25
    assert child["status"] == "ok"
    assert child["actual_model"] == "original"
