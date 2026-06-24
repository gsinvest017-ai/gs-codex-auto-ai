"""Tests for src/codexautoai_v2/events.py"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.codexautoai_v2.events import EventBus, redact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_log() -> str:
    """Return a temp file path that does not yet exist."""
    fd, path = tempfile.mkstemp(suffix='.jsonl')
    os.close(fd)
    os.unlink(path)  # We want the bus to create (or append to) it
    return path


def _read_lines(path: str) -> list[dict]:
    with open(path, encoding='utf-8') as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------------------
# redact()
# ---------------------------------------------------------------------------

class TestRedact:
    def test_masks_sk_key(self):
        text = "Using key sk-abc123XYZ to call the API"
        result = redact(text)
        assert 'sk-abc123XYZ' not in result
        assert '***' in result

    def test_masks_password_equals(self):
        text = "Connect with password=supersecret now"
        result = redact(text)
        assert 'supersecret' not in result
        assert '***' in result

    def test_masks_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6"
        result = redact(text)
        assert 'eyJhbGciOiJSUzI1NiIsInR5cCI6' not in result
        assert '***' in result

    def test_leaves_plain_text_alone(self):
        text = "Hello world, nothing secret here."
        assert redact(text) == text

    def test_non_string_passthrough(self):
        assert redact(42) == 42  # type: ignore[arg-type]
        assert redact(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# EventBus.emit — OBS-R1: timestamp always from system clock
# ---------------------------------------------------------------------------

class TestEmitTimestamp:
    def test_ignores_caller_supplied_timestamp(self):
        """OBS-R1: even if caller passes a bogus timestamp it must be ignored."""
        path = _tmp_log()
        try:
            bus = EventBus(path)
            bogus_ts = "1970-01-01T00:00:00+00:00"
            event = bus.emit("test_event", timestamp=bogus_ts, some_field="value")
            # The returned event's timestamp must NOT be the bogus value
            assert event["timestamp"] != bogus_ts, (
                "emit() must stamp from the system clock, not the caller value"
            )
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_timestamp_is_utc_iso(self):
        import re
        path = _tmp_log()
        try:
            bus = EventBus(path)
            event = bus.emit("test_event")
            ts = event["timestamp"]
            pattern = re.compile(
                r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$'
            )
            assert pattern.match(ts), f"Bad timestamp: {ts!r}"
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ---------------------------------------------------------------------------
# EventBus.emit — JSONL persistence
# ---------------------------------------------------------------------------

class TestEmitJsonl:
    def test_appends_valid_json_line(self):
        path = _tmp_log()
        try:
            bus = EventBus(path)
            bus.emit("file_op", filename="report.txt", size=1024)
            lines = _read_lines(path)
            assert len(lines) == 1
            assert lines[0]["event_type"] == "file_op"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_multiple_events_are_separate_lines(self):
        path = _tmp_log()
        try:
            bus = EventBus(path)
            bus.emit("ev1")
            bus.emit("ev2")
            bus.emit("ev3")
            lines = _read_lines(path)
            assert len(lines) == 3
            assert lines[0]["event_type"] == "ev1"
            assert lines[2]["event_type"] == "ev3"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_event_dict_returned_matches_jsonl(self):
        path = _tmp_log()
        try:
            bus = EventBus(path)
            returned = bus.emit("check", value=99)
            persisted = _read_lines(path)[0]
            assert returned == persisted
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_string_fields_redacted_in_jsonl(self):
        path = _tmp_log()
        try:
            bus = EventBus(path)
            bus.emit("api_call", api_key="sk-secretkey123456")
            lines = _read_lines(path)
            assert 'sk-secretkey123456' not in json.dumps(lines[0])
            assert '***' in json.dumps(lines[0])
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ---------------------------------------------------------------------------
# EventBus.emit_llm_call — OBS-R2, OBS-R3, OBS-R4
# ---------------------------------------------------------------------------

class TestEmitLlmCall:
    def test_token_fields_present(self):
        path = _tmp_log()
        try:
            bus = EventBus(path)
            event = bus.emit_llm_call(
                model="claude-3",
                input_tokens=100,
                output_tokens=50,
                iteration=2,
                retries=1,
                duration_ms=350,
                status="ok",
            )
            assert event["gen_ai.request.model"] == "claude-3"
            assert event["gen_ai.usage.input_tokens"] == 100
            assert event["gen_ai.usage.output_tokens"] == 50
            assert event["iteration"] == 2
            assert event["retries"] == 1
            assert event["duration_ms"] == 350
            assert event["status"] == "ok"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_prompt_omitted_when_store_content_false(self):
        """OBS-R4: prompt/completion must not appear when store_content=False."""
        path = _tmp_log()
        try:
            bus = EventBus(path, store_content=False)
            event = bus.emit_llm_call(
                model="gpt-4",
                input_tokens=200,
                output_tokens=80,
                prompt="This is a secret user prompt",
                completion="This is the model response",
            )
            assert "prompt" not in event
            assert "completion" not in event
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_prompt_included_when_store_content_true(self):
        path = _tmp_log()
        try:
            bus = EventBus(path, store_content=True)
            event = bus.emit_llm_call(
                model="gpt-4",
                input_tokens=10,
                output_tokens=5,
                prompt="Hello there",
                completion="Hi back",
            )
            assert event.get("prompt") == "Hello there"
            assert event.get("completion") == "Hi back"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_prompt_secrets_redacted_when_store_content_true(self):
        path = _tmp_log()
        try:
            bus = EventBus(path, store_content=True)
            event = bus.emit_llm_call(
                model="model-x",
                input_tokens=5,
                output_tokens=5,
                prompt="Use key sk-topsecret999 please",
                completion="Done",
            )
            assert 'sk-topsecret999' not in event.get("prompt", "")
            assert '***' in event.get("prompt", "")
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_event_type_is_llm_call(self):
        path = _tmp_log()
        try:
            bus = EventBus(path)
            event = bus.emit_llm_call("model-y", 1, 1)
            assert event["event_type"] == "llm_call"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_jsonl_line_valid_json(self):
        path = _tmp_log()
        try:
            bus = EventBus(path)
            bus.emit_llm_call("model-z", 30, 20, duration_ms=100)
            lines = _read_lines(path)
            assert len(lines) == 1
            assert lines[0]["gen_ai.usage.input_tokens"] == 30
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ---------------------------------------------------------------------------
# EventBus.emit_tool_call
# ---------------------------------------------------------------------------

class TestEmitToolCall:
    def test_basic_fields(self):
        path = _tmp_log()
        try:
            bus = EventBus(path)
            event = bus.emit_tool_call("bash", status="ok", duration_ms=42)
            assert event["event_type"] == "tool_call"
            assert event["tool"] == "bash"
            assert event["status"] == "ok"
            assert event["duration_ms"] == 42
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_extra_fields_redacted(self):
        path = _tmp_log()
        try:
            bus = EventBus(path)
            event = bus.emit_tool_call(
                "file_write",
                status="ok",
                note="Used password=hunter2 internally",
            )
            assert 'hunter2' not in event.get("note", "")
        finally:
            if os.path.exists(path):
                os.unlink(path)
