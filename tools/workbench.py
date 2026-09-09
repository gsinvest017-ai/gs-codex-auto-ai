"""Workspace-confined workbench data API, shared by CLI, MCP and UI adapters.

Only provider-emitted messages/tools and local file evidence are exposed. Reasoning
blocks and inferred progress are deliberately excluded. Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
import os
import re
from pathlib import Path
import sys
import time
import uuid

try:
    from . import model_router, events_model
    from .codex_runner import output_metadata
except ImportError:
    import model_router
    import events_model
    from codex_runner import output_metadata

FORMATS = {".glb", ".gltf", ".obj", ".png"}
MAX_LOG_BYTES = 4 * 1024 * 1024
IGNORED_DIRS = {".git", ".venv", "venv", "vendor", ".claude", ".codex", "node_modules", "__pycache__", "log"}
MAX_SCAN_FILES = 20000
MAX_SCAN_DIRS = 2000


def _validated_graph_result(graph, run, exit_code=None):
    """Parity contract with task-evidence.validatedGraphResult; no artifact rehash."""
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    try:
        route = run['route']
        nodes, edges = route['graph_snapshot']['nodes'], route['graph_snapshot']['edges']
        states, activated = graph['graph_states'], graph['activated_edges']
        return bool(exit_code in (None, 0) and finite(run['started_at'])
                    and type(graph['schema_version']) is int and graph['schema_version'] == 1
                    and graph['run_id'] == run['run_id'] and graph['graph_id'] == route['graph_id']
                    and graph['graph_digest'] == route['graph_digest']
                    and re.fullmatch(r'[a-f0-9]{64}', graph['graph_digest'])
                    and finite(graph['started_at']) and finite(graph['ended_at'])
                    and graph['started_at'] >= run['started_at'] and graph['ended_at'] >= graph['started_at']
                    and graph['status'] in ('completed', 'blocked') and graph['task_delivery_verified'] is False
                    and isinstance(states, dict) and isinstance(nodes, list) and len(states) == len(nodes)
                    and all(states.get(n['id']) in ('ok', 'skipped', 'failed', 'quota_exhausted') for n in nodes)
                    and isinstance(activated, list) and all(any(e['id'] == key for e in edges) for key in activated)
                    and (graph['status'] != 'completed' or ('ok' in states.values() and all(
                        states[n['id']] not in ('failed', 'quota_exhausted') or any(
                            e['source'] == n['id'] and e['id'] in activated and e['condition'] == 'quota_exhausted'
                            and states[n['id']] == 'quota_exhausted' for e in edges) for n in nodes))))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


class Workbench:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("workspace root must be a directory")

    def confined(self, value: str | Path, *, exists: bool = True) -> Path:
        path = Path(value)
        resolved = (path if path.is_absolute() else self.root / path).resolve(strict=exists)
        if not resolved.is_relative_to(self.root):
            raise ValueError("path is outside the workspace")
        return resolved

    def _events(self) -> list[dict]:
        path = self.confined("log/events.jsonl", exists=False)
        return events_model.read_events(path)

    def _append(self, name: str, payload: dict) -> dict:
        path = self.confined("log/" + name, exists=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        return payload

    def catalog(self) -> dict:
        self.confined("log/model-routing.json", exists=False)
        return model_router.catalog(self.root)

    def preview(self, prompt: str, scenario: str | None = None) -> dict:
        if not isinstance(prompt, str) or len(prompt) > 100_000:
            raise ValueError("prompt must be text up to 100000 characters")
        self.confined("log/model-routing.json", exists=False)
        return model_router.resolve_route(prompt, self.root, scenario=scenario)

    def _scope(self, run_id: str | None = None) -> tuple[str | None, list[dict]]:
        events = self._events()
        if run_id is None:
            app = self.confined("log/app-run.json", exists=False)
            try:
                value = json.loads(app.read_text(encoding="utf-8-sig"))
                run_id = value.get("run_id") if isinstance(value, dict) else None
            except (OSError, ValueError):
                pass
        attempts = [e for e in events if (e.get("type") or e.get("event_type")) == "model_attempt"]
        if run_id is None and attempts:
            run_id = attempts[-1].get("parent_run_id") or attempts[-1].get("run_id")
        selected = [e for e in attempts if run_id and run_id in (e.get("run_id"), e.get("parent_run_id"))]
        latest = {}
        for event in selected:
            if event.get("attempt_id"):
                latest[event["attempt_id"]] = {**latest.get(event["attempt_id"], {}), **event}
        return run_id, list(latest.values())

    def status(self, run_id: str | None = None) -> dict:
        scope, attempts = self._scope(run_id)
        active = [a for a in attempts if a.get("outcome") == "started" and a.get("role") != "native_worker"]
        states = [a.get("outcome") for a in attempts if a.get("role") != "native_worker"]
        state = "running" if active else states[-1] if states else "unknown"
        task = self._task_result(scope)
        task_status = task.get("status") or "unknown"
        return {"run_id": scope, "status": task_status, "task_status": task_status,
                "call_status": state, "task_result": task, "attempts": attempts,
                "metrics": events_model.routing_stats(self._events(), parent_run_id=scope)
                if any(a.get("parent_run_id") == scope for a in attempts)
                else events_model.routing_stats(self._events(), run_id=scope),
                "source": "log/events.jsonl", "observed_at": time.time(),
                "progress": self._progress(scope)}

    def _task_result(self, scope: str | None) -> dict:
        """Mirror routing.js taskResult's persisted delivery-envelope contract.

        Hashes prove delivery-time verification by the runner; historical completion
        does not rehash subsequently edited files. No phase or metrics parser here.
        """
        if not scope:
            return {}
        app, candidate = {}, {}
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", scope)
        candidate_present = False
        for relative, target in (("log/app-run.json", "app"),
                                 (f"log/task-result-{safe_id}.json", "result")):
            try:
                value = json.loads(self.confined(relative).read_text(encoding="utf-8-sig"))
                if target == "result":
                    candidate_present = True
                if isinstance(value, dict) and value.get("run_id") == scope:
                    if target == "app":
                        app = value
                    else:
                        candidate = value
            except (OSError, ValueError):
                pass
        if not app:
            try:
                session_path = self.confined('log/vscode-sessions.jsonl')
                with session_path.open('rb') as handle:
                    handle.seek(max(0, session_path.stat().st_size - MAX_LOG_BYTES))
                    for line in handle.read(MAX_LOG_BYTES).splitlines():
                        try:
                            record = json.loads(line)
                            if isinstance(record, dict) and record.get('run_id') == scope:
                                app = record
                        except ValueError:
                            pass
            except (OSError, ValueError):
                pass
        if isinstance(app.get('route'), dict) and app['route'].get('mode') == 'graph':
            exit_code = None
            try:
                if app.get('exit_file'):
                    exit_code = int(self.confined(app['exit_file']).read_text(encoding='utf-8-sig').strip())
            except (OSError, ValueError):
                pass
            try:
                graph = json.loads(self.confined(f'log/graph-result-{safe_id}.json').read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                graph = None
            valid = _validated_graph_result(graph, app, exit_code)
            return {'run_id': scope, 'status': ('failed' if exit_code not in (None, 0) else graph['status'] if valid else 'incomplete'),
                    'source': 'graph_result', 'execution_mode': 'graph', 'task_delivery_verified': False,
                    'reason': '接線執行結果；未驗證七階段交付。', 'graph': graph if valid else None}
        def finite(value):
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        started, ended = candidate.get("started_at"), candidate.get("ended_at")
        baseline = app.get("started_at", started)
        valid = (candidate.get("schema_version") == 1 and not isinstance(candidate.get("schema_version"), bool)
                 and finite(started) and finite(ended) and finite(baseline)
                 and started >= baseline and ended >= started
                 and candidate.get("status") in ("completed", "blocked", "incomplete", "failed"))
        if valid and candidate["status"] != "completed":
            return {**candidate, "source": "task_result", "evidence_validation": "valid_envelope"}
        if valid:
            evidence = candidate.get("completion_evidence")
            if isinstance(evidence, dict):
                try:
                    stamp = datetime.fromisoformat(str(evidence.get("ts") or evidence.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError, OverflowError):
                    stamp = None
                artifacts = evidence.get("artifacts")
                phase = str(evidence.get("phase", "")).removeprefix("phase")
                delivered = (evidence.get("run_id") == scope and stamp is not None and stamp >= baseline
                             and (evidence.get("event_type") or evidence.get("type")) == "phase_end"
                             and phase == "7" and evidence.get("status") == "success"
                             and isinstance(artifacts, list) and bool(artifacts)
                             and all(isinstance(item, dict) and isinstance(item.get("path"), str)
                                     and bool(item["path"]) and isinstance(item.get("sha256"), str)
                                     and re.fullmatch(r"[a-fA-F0-9]{64}", item["sha256"])
                                     for item in artifacts))
                if delivered:
                    return {**candidate, "source": "task_result", "evidence_validation": "valid_delivery_envelope"}
        if candidate_present or app.get("status") == "completed":
            return {"run_id": scope, "status": "incomplete", "source": "delivery_validation",
                    "reason": "missing_verified_phase7_delivery", "evidence_validation": "invalid_or_missing"}
        # Running/blocked/failed app state is observational, never completion proof.
        return {**app, "source": "app_run"} if app else {}

    def _progress(self, run_id: str | None) -> list[dict]:
        path = self.confined("log/workbench-progress.jsonl", exists=False)
        return [e for e in events_model.read_events(path) if e.get("run_id") == run_id][-50:]

    def activity(self, run_id: str | None = None, limit: int = 100) -> dict:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer from 1 to 200")
        scope, attempts = self._scope(run_id)
        items, usage, limited, last_update = [], {}, False, None
        for attempt in attempts:
            raw = attempt.get("result_path")
            if not raw:
                continue
            try:
                path = self.confined(raw)
                stat = path.stat()
                size = stat.st_size
                last_update = max(last_update or 0, stat.st_mtime)
                with path.open("rb") as handle:
                    offset = max(0, size - MAX_LOG_BYTES)
                    handle.seek(offset)
                    if offset:
                        handle.readline()
                        offset = handle.tell()
                    data = handle.read(MAX_LOG_BYTES)
            except (OSError, ValueError):
                continue
            text = data.decode("utf-8", errors="replace")
            complete = offset == 0
            limited |= not complete
            # A truncated tail cannot claim full-call usage totals.
            usage[attempt["attempt_id"]] = {**output_metadata(text if complete else ""),
                                            "complete": complete, "source_path": str(path)}
            line_number = 0 if complete else None
            for raw_line in data.splitlines(keepends=True):
                at = offset
                offset += len(raw_line)
                if line_number is not None:
                    line_number += 1
                try:
                    event = json.loads(raw_line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                for index, entry in enumerate(_activity_entries(event)):
                    items.append({"id": f"{attempt['attempt_id']}:{at}:{index}",
                                  "attempt_id": attempt["attempt_id"], "provider": attempt.get("actual_provider"),
                                  "event_type": event.get("type"), "historical": attempt.get("outcome") != "started",
                                  "item_id": (event.get("item", {}).get("id") if isinstance(event.get("item"), dict) else None),
                                  "source_path": str(path), "source_line": line_number,
                                  "source_offset": at, "source_mtime": stat.st_mtime,
                                  "timestamp": event.get("timestamp") or event.get("ts"),
                                  "summary": entry["text"], **entry})
        unique = {}
        for item in items:
            identity = f"{item['attempt_id']}:item:{item['item_id']}" if item.get("item_id") else item["id"]
            item["id"] = identity
            if item["historical"] and item.get("status") in ("in_progress", "started", "item.started", "running"):
                item["reported_status"] = item["status"]
                item["status"] = "historical"
            unique[identity] = item
        items = list(unique.values())
        limited |= len(items) > limit
        return {"run_id": scope, "items": items[-limit:], "usage_by_attempt": usage,
                "source": "provider_cli_output", "limited": limited, "last_update": last_update,
                "observed_at": time.time(), "reasoning_exposed": False}

    def artifacts(self) -> dict:
        items, limited, visited_files, visited_dirs = [], False, 0, 0
        for parent, dirs, files in os.walk(self.root, followlinks=False):
            visited_dirs += 1
            if visited_dirs > MAX_SCAN_DIRS:
                limited = True
                break
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS
                             and not Path(parent, d).is_symlink()
                             and not (hasattr(Path(parent, d), "is_junction") and Path(parent, d).is_junction()))
            for name in sorted(files):
                visited_files += 1
                if visited_files > MAX_SCAN_FILES:
                    limited = True
                    break
                if Path(name).suffix.lower() not in FORMATS:
                    continue
                try:
                    path = self.confined(Path(parent, name))
                    if not path.is_file():
                        continue
                    stat = path.stat()
                except (OSError, ValueError):
                    continue
                items.append({"path": str(path), "relative_path": path.relative_to(self.root).as_posix(),
                              "format": path.suffix.lower()[1:], "size": stat.st_size, "mtime": stat.st_mtime,
                              "source": "workspace_file"})
                if len(items) >= 2000:
                    limited = True
                    break
            if limited:
                break
        registrations = []
        path = self.confined("log/workbench-artifacts.jsonl", exists=False)
        indexed = {item["relative_path"] for item in items}
        for entry in events_model.read_events(path):
            try:
                registered = self.confined(entry.get("relative_path", ""))
                if not registered.is_file() or registered.suffix.lower() not in FORMATS:
                    continue
            except (OSError, ValueError):
                continue
            registrations.append(entry)
            relative = registered.relative_to(self.root).as_posix()
            if relative not in indexed:
                stat = registered.stat()
                items.append({"path": str(registered), "relative_path": relative,
                              "format": registered.suffix.lower()[1:], "size": stat.st_size,
                              "mtime": stat.st_mtime, "source": "registered_local_file"})
                indexed.add(relative)
        return {"items": items, "registrations": registrations[-2000:],
                "source": "workspace_scan+registry", "limited": limited}

    def report_progress(self, message: str, run_id: str | None = None,
                        progress: float | None = None) -> dict:
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise ValueError("message must contain 1 to 4000 characters")
        if progress is not None and (isinstance(progress, bool) or not isinstance(progress, (int, float)) or not 0 <= progress <= 100):
            raise ValueError("progress must be a number between 0 and 100, or null")
        scope, _ = self._scope(run_id)
        if not scope:
            raise ValueError("a run_id is required when no run has been observed")
        return self._append("workbench-progress.jsonl", {
            "id": uuid.uuid4().hex, "run_id": scope, "message": message,
            "progress": progress, "source": "caller_reported", "verified": False, "reported_at": time.time()})

    def register_artifact(self, path: str, run_id: str | None = None,
                          label: str | None = None) -> dict:
        file = self.confined(path)
        if not file.is_file() or file.suffix.lower() not in FORMATS:
            raise ValueError("artifact must be a local GLB, glTF, OBJ or PNG file")
        if label is not None and (not isinstance(label, str) or len(label) > 400):
            raise ValueError("label must be text up to 400 characters")
        digest = hashlib.sha256()
        with file.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        scope, _ = self._scope(run_id)
        stat = file.stat()
        return self._append("workbench-artifacts.jsonl", {
            "id": uuid.uuid4().hex, "run_id": scope, "path": str(file),
            "relative_path": file.relative_to(self.root).as_posix(), "format": file.suffix.lower()[1:],
            "label": label, "size": stat.st_size, "mtime": stat.st_mtime,
            "sha256": digest.hexdigest(), "registered_at": time.time(),
            "source": "registered_local_file", "snapshot": False})


def _activity_entries(event: dict) -> list[dict]:
    """Allowlist public CLI event types, never reasoning/thinking blocks."""
    kind = event.get("type")
    item = event.get("item")
    if kind in ("item.started", "item.updated", "item.completed") and isinstance(item, dict):
        subtype = item.get("type")
        if subtype == "agent_message":
            return [{"kind": "agent_message", "text": str(item.get("text", ""))[:8000], "status": "observed"}]
        if subtype in ("command_execution", "mcp_tool_call", "web_search", "file_change", "collab_tool_call"):
            text = item.get("command") or item.get("tool") or item.get("query") or json.dumps(item.get("changes", []), ensure_ascii=False)
            return [{"kind": subtype, "text": str(text)[:4000], "status": item.get("status", kind)}]
    if kind == "assistant":
        message = event.get("message", {})
        content = message.get("content", []) if isinstance(message, dict) else []
        return [{"kind": "agent_message" if block["type"] == "text" else "tool_call",
                 "text": str(block.get("text") or block.get("name", ""))[:8000], "status": "observed"}
                for block in content if isinstance(block, dict) and block.get("type") in ("text", "tool_use")]
    if kind in ("text", "tool_use") and isinstance(event.get("part"), dict):
        part = event["part"]
        return [{"kind": "agent_message" if kind == "text" else "tool_call",
                 "text": str(part.get("text") or part.get("tool", ""))[:8000], "status": "observed"}]
    if kind == "result" and isinstance(event.get("result"), str):
        return [{"kind": "agent_message", "text": event["result"][:8000], "status": "observed"}]
    return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--action", choices=("catalog", "preview", "status", "activity", "artifacts", "report_progress", "register_artifact"), required=True)
    for arg in ("prompt", "scenario", "run-id", "path", "message", "label"):
        parser.add_argument("--" + arg)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--progress", type=float)
    args = parser.parse_args(argv)
    try:
        workbench = Workbench(args.root)
        arguments = {"preview": {"prompt": args.prompt, "scenario": args.scenario},
                     "status": {"run_id": args.run_id}, "activity": {"run_id": args.run_id, "limit": args.limit},
                     "report_progress": {"message": args.message, "run_id": args.run_id, "progress": args.progress},
                     "register_artifact": {"path": args.path, "run_id": args.run_id, "label": args.label}}.get(args.action, {})
        result = getattr(workbench, args.action)(**arguments)
        code = 0
    except (OSError, ValueError, TypeError) as exc:
        result, code = {"error": str(exc)}, 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
