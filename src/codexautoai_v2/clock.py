"""
clock.py — System clock utilities for CodexAutoAI v2.

OBS-R1: All timestamps come from the SYSTEM CLOCK.
Do NOT use any timestamp provided by a caller/LLM as the event time.
"""

from datetime import datetime, timezone


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (system clock)."""
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> float:
    """Return the current UTC time as epoch seconds (system clock)."""
    return datetime.now(timezone.utc).timestamp()
