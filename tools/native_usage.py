"""Read native child evidence once after a CLI run; never infer it from model text."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _events(path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        yield event
                except (ValueError, TypeError):
                    continue
    except OSError:
        return


def _timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _identity(path):
    # Only the first identity belongs to this file. Forked history includes others.
    for index, event in enumerate(_events(path)):
        if event.get("type") == "session_meta":
            value = event.get("payload")
            return value if isinstance(value, dict) else {}
        if index >= 7:
            break
    return {}


def _child(path, meta, started_at, ended_at):
    source = meta["source"]["subagent"]["thread_spawn"]
    record = {"thread_id": meta["id"], "parent_thread_id": source["parent_thread_id"],
              "agent_path": source.get("agent_path"), "actual_model": None,
              "usage": {"input_tokens": None, "output_tokens": None, "cached_input_tokens": None},
              "status": "unknown", "usage_source": "codex_rollout", "source_path": str(path)}
    own = False
    for event in _events(path):
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        # A fork repeats parent context and token totals. This runtime event is
        # explicit proof that subsequent records belong to the child itself.
        if (event.get("type") == "event_msg" and payload.get("type") == "thread_settings_applied"
                and payload.get("thread_id") == meta["id"]):
            own = True
        stamp = _timestamp(event.get("timestamp"))
        if not own or stamp is None or not started_at <= stamp <= ended_at:
            continue
        if payload.get("type") == "thread_settings_applied":
            settings = payload.get("thread_settings", {})
            if isinstance(settings, dict) and isinstance(settings.get("model"), str):
                record["actual_model"] = settings["model"]
        if event.get("type") == "turn_context" and isinstance(payload.get("model"), str):
            record["actual_model"] = payload["model"]
        if event.get("type") != "event_msg":
            continue
        kind = payload.get("type")
        if kind == "token_count":
            info = payload.get("info") or {}
            total = info.get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(total, dict):
                # Cumulative totals: replace, never sum consecutive snapshots.
                record["usage"] = {key: value if isinstance(value, int) and not isinstance(value, bool)
                                   and value >= 0 else None
                                   for key in record["usage"] for value in [total.get(key)]}
        elif kind == "task_started":
            record["status"] = "unknown"
        elif kind == "task_complete":
            record["status"] = "ok"
        elif kind in ("task_aborted", "turn_aborted", "task_failed"):
            record["status"] = "failed"
    return record


def collect_native_children(result_path, sessions_dir, started_at, ended_at=None):
    """Return provably linked descendants, with unknowns when evidence is absent.

    Date-directory discovery and header filtering happen once after completion.
    JSONL contents are streamed, and unrelated histories are never read in full.
    """
    root_id = next((event.get("thread_id") for event in _events(result_path)
                    if event.get("type") == "thread.started"), None)
    if not isinstance(root_id, str) or not root_id:
        return []
    ended_at = datetime.now(timezone.utc).timestamp() if ended_at is None else ended_at
    if ended_at < started_at:
        return []
    # CLI date folders follow local time; +/- one day covers UTC offsets.
    first = datetime.fromtimestamp(started_at, timezone.utc).date() - timedelta(days=1)
    last = datetime.fromtimestamp(ended_at, timezone.utc).date() + timedelta(days=1)
    candidates = {}
    day = first
    while day <= last:
        folder = Path(sessions_dir) / day.strftime("%Y/%m/%d")
        for path in folder.glob("rollout-*.jsonl"):
            meta = _identity(path)
            stamp = _timestamp(meta.get("timestamp"))
            source = meta.get("source")
            subagent = source.get("subagent") if isinstance(source, dict) else None
            spawn = subagent.get("thread_spawn", {}) if isinstance(subagent, dict) else {}
            if (stamp is not None and started_at <= stamp <= ended_at and isinstance(spawn, dict)
                    and isinstance(meta.get("id"), str) and isinstance(spawn.get("parent_thread_id"), str)):
                candidates.setdefault(meta["id"], (path, meta, spawn["parent_thread_id"]))
        day += timedelta(days=1)
    accepted, results = {root_id}, []
    while True:
        linked = [(tid, item) for tid, item in candidates.items()
                  if tid not in accepted and item[2] in accepted]
        if not linked:
            break
        for tid, (path, meta, _) in linked:
            accepted.add(tid)
            results.append(_child(path, meta, started_at, ended_at))
    return results
