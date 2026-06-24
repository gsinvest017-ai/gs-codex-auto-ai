"""
audit.py — Append-only tamper-evident audit log for CodexAutoAI v2.

SECGOV-R8 + observability C8:
  Each entry stores prev_hash and its own hash = sha256(prev_hash + canonical_json(payload)).
  verify() detects any modification, insertion, deletion, or reordering of entries.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Genesis constant: the "previous hash" of the very first entry.
GENESIS = "0" * 64


def _canonical(payload: dict) -> str:
    """Return a deterministic JSON string for hashing (sorted keys, no whitespace)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _compute_hash(prev_hash: str, payload: dict) -> str:
    """sha256(prev_hash + canonical_json(payload)) -> hex digest."""
    data = (prev_hash + _canonical(payload)).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class AuditLog:
    """Append-only tamper-evident audit log backed by a JSONL file."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, payload: dict) -> str:
        """
        Append a new entry to the log.

        Returns the new entry's hash.
        Each persisted line is a JSON object:
          { "seq": int, "prev_hash": str, "payload": dict, "hash": str }
        """
        existing = self._load_raw()
        if existing:
            last = existing[-1]
            prev_hash: str = last["hash"]
            seq: int = last["seq"] + 1
        else:
            prev_hash = GENESIS
            seq = 0

        entry_hash = _compute_hash(prev_hash, payload)
        entry = {
            "seq": seq,
            "prev_hash": prev_hash,
            "payload": payload,
            "hash": entry_hash,
        }
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return entry_hash

    def entries(self) -> list[dict]:
        """Return all log entries as a list of dicts."""
        return self._load_raw()

    def verify(self) -> bool:
        """
        Recompute the hash chain from the beginning.

        Returns True if the chain is intact, False if any entry has been
        modified, inserted, deleted, or reordered.
        """
        raw = self._load_raw()
        if not raw:
            return True

        expected_prev = GENESIS
        for i, entry in enumerate(raw):
            # Sequence numbers must be contiguous starting at 0
            if entry.get("seq") != i:
                return False
            # prev_hash must match expected
            if entry.get("prev_hash") != expected_prev:
                return False
            # Stored hash must match recomputed hash
            recomputed = _compute_hash(entry["prev_hash"], entry["payload"])
            if entry.get("hash") != recomputed:
                return False
            expected_prev = entry["hash"]
        return True

    def head(self) -> str:
        """
        Return the current head hash (the hash of the last entry).
        Returns GENESIS constant if the log is empty.
        """
        raw = self._load_raw()
        if not raw:
            return GENESIS
        return raw[-1]["hash"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_raw(self) -> list[dict]:
        """Read all JSONL lines from disk and return parsed entries."""
        if not self._path.exists():
            return []
        entries: list[dict] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries
