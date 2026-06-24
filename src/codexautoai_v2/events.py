"""
events.py — Structured JSONL event bus for CodexAutoAI v2.

Implements OBS-R1 through OBS-R4 / SECGOV-R3:
  OBS-R1  : Timestamps always come from the system clock; caller-supplied
             timestamp values are ignored.
  OBS-R2  : One structured JSONL event per LLM call / tool call / file op,
             following OpenTelemetry GenAI conventions.
  OBS-R3  : Per-loop metrics observable via iteration / retries fields.
  OBS-R4  : Privacy — prompt/completion content NOT persisted by default.
  SECGOV-R3: Secrets (API keys, bearer tokens, passwords) are redacted.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Secret-redaction patterns
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    # sk-... style API keys (OpenAI, Anthropic, etc.)
    re.compile(r'\bsk-[A-Za-z0-9_\-]{6,}', re.IGNORECASE),
    # Bearer tokens
    re.compile(r'\bBearer\s+[A-Za-z0-9._\-/+]{8,}', re.IGNORECASE),
    # password=<value>  (word boundary before, non-whitespace value)
    re.compile(r'(?i)(password\s*=\s*)\S+'),
    # key=<value>
    re.compile(r'(?i)((?<!\w)key\s*=\s*)\S+'),
    # Authorization header value
    re.compile(r'(?i)(Authorization\s*:\s*)\S+'),
]


def redact(text: str) -> str:
    """
    Return *text* with secrets replaced by '***'.

    Patterns masked:
    - sk-... API keys
    - Bearer <token>
    - password=<value>
    - key=<value>
    - Authorization: <value>
    """
    if not isinstance(text, str):
        return text

    result = text
    for pattern in _SECRET_PATTERNS:
        # Keep the leading key/label group (group 1) when present; replace
        # only the secret value part.
        if pattern.groups:
            result = pattern.sub(lambda m: m.group(1) + '***', result)
        else:
            result = pattern.sub('***', result)
    return result


# ---------------------------------------------------------------------------
# Private clock helper (self-contained; does NOT import clock.py)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

class EventBus:
    """
    Append-only JSONL event bus.

    Parameters
    ----------
    path : str
        File path where JSONL events are appended.
    store_content : bool
        When False (default) prompt/completion text is dropped (OBS-R4).
    """

    def __init__(self, path: str, store_content: bool = False) -> None:
        self._path = path
        self._store_content = store_content
        # Ensure parent directory exists
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, event_type: str, *, timestamp=None, **fields) -> dict:
        """
        Build, persist, and return one JSONL event.

        OBS-R1: *timestamp* parameter is accepted but ALWAYS ignored; the
        event's timestamp is stamped from the system clock.

        All string field values are passed through redact().
        """
        # OBS-R1: always use system clock; ignore any caller-supplied value
        event: dict = {
            "event_type": event_type,
            "timestamp": _now_iso(),   # system clock — caller value discarded
        }

        for k, v in fields.items():
            if isinstance(v, str):
                event[k] = redact(v)
            else:
                event[k] = v

        self._append(event)
        return event

    def emit_llm_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        iteration: int = 0,
        retries: int = 0,
        duration_ms: float = 0,
        status: str = 'ok',
        prompt: str | None = None,
        completion: str | None = None,
    ) -> dict:
        """
        Emit one LLM-call event following OpenTelemetry GenAI conventions.

        OBS-R4: prompt and completion are dropped unless store_content=True.
        """
        fields: dict = {
            "gen_ai.request.model": redact(model),
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "iteration": iteration,
            "retries": retries,
            "duration_ms": duration_ms,
            "status": redact(status) if isinstance(status, str) else status,
        }

        if self._store_content:
            if prompt is not None:
                fields["prompt"] = redact(prompt)
            if completion is not None:
                fields["completion"] = redact(completion)
        # else: drop prompt/completion entirely (OBS-R4)

        event: dict = {
            "event_type": "llm_call",
            "timestamp": _now_iso(),
        }
        event.update(fields)

        self._append(event)
        return event

    def emit_tool_call(
        self,
        tool: str,
        *,
        status: str = 'ok',
        duration_ms: float = 0,
        **fields,
    ) -> dict:
        """Emit one tool-call event."""
        extra: dict = {}
        for k, v in fields.items():
            extra[k] = redact(v) if isinstance(v, str) else v

        event: dict = {
            "event_type": "tool_call",
            "timestamp": _now_iso(),
            "tool": redact(tool),
            "status": redact(status) if isinstance(status, str) else status,
            "duration_ms": duration_ms,
        }
        event.update(extra)

        self._append(event)
        return event

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _append(self, event: dict) -> None:
        """Append *event* as a single JSON line to self._path."""
        with open(self._path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + '\n')
