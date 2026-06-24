"""
state.py — Action-level checkpoint and resume for CodexAutoAI v2.

Implements:
  STATE-R1: Persist run state after every phase and every action;
            resumed run (same run_id) continues from exact interrupted action.
  STATE-R2: Deterministic replay — ordered list of completed actions.
  STATE-R3: Exactly-once — idempotency keys for irreversible side effects.
  SECGOV-R8: Schema-validate on load; reject malformed/tampered state.
"""

import json
import os
import hashlib
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1

_REQUIRED_KEYS = {"schema_version", "run_id", "phase", "completed_actions", "side_effects"}


class StateError(Exception):
    """Raised on malformed, tampered, or incompatible state files."""


class RunState:
    """Checkpoint-aware run state for a single pipeline execution."""

    def __init__(self, run_id: str, phase: str = "init") -> None:
        if not isinstance(run_id, str) or not run_id:
            raise StateError("run_id must be a non-empty string")
        self.run_id: str = run_id
        self.phase: str = phase
        # ordered list — preserves insertion order (STATE-R2)
        self._completed: list[str] = []
        # set for O(1) membership check
        self._completed_set: set[str] = set()
        # ordered list of idempotency keys (STATE-R3)
        self._side_effects: list[str] = []
        self._side_effects_set: set[str] = set()

    # ------------------------------------------------------------------ actions

    def mark_done(self, action_id: str) -> None:
        """Record action_id as completed (idempotent — safe to call twice)."""
        if action_id not in self._completed_set:
            self._completed.append(action_id)
            self._completed_set.add(action_id)

    def is_done(self, action_id: str) -> bool:
        """Return True if action_id has been completed."""
        return action_id in self._completed_set

    def completed_actions(self) -> list[str]:
        """Return ordered list of completed action IDs (STATE-R2)."""
        return list(self._completed)

    # ------------------------------------------------------------------ phase

    def set_phase(self, phase: str) -> None:
        """Update the current phase name."""
        self.phase = phase

    # ------------------------------------------------------------------ side-effects

    def record_side_effect(self, idempotency_key: str) -> None:
        """Record that an irreversible side effect has been applied (STATE-R3)."""
        if idempotency_key not in self._side_effects_set:
            self._side_effects.append(idempotency_key)
            self._side_effects_set.add(idempotency_key)

    def already_applied(self, idempotency_key: str) -> bool:
        """Return True if this idempotency key has already been recorded."""
        return idempotency_key in self._side_effects_set

    # ------------------------------------------------------------------ persistence

    def _to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "phase": self.phase,
            "completed_actions": list(self._completed),
            "side_effects": list(self._side_effects),
        }

    def checkpoint(self, path: str) -> None:
        """Atomically persist state to *path* (STATE-R1).

        Uses write-to-temp + rename for atomicity on Windows/POSIX.
        Includes schema_version for SECGOV-R8 validation on load.
        """
        data = self._to_dict()
        payload = json.dumps(data, ensure_ascii=False, indent=2)

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Write to a sibling temp file, then rename — atomic on same filesystem
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent), prefix=".state_tmp_", suffix=".json"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(payload)
            # On Windows, os.replace handles the atomic swap
            os.replace(tmp_path, str(target))
        except Exception:
            # Best-effort cleanup of the temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str) -> "RunState":
        """Load and validate state from *path* (SECGOV-R8).

        Raises StateError if:
        - File does not exist or cannot be decoded as JSON
        - Required keys are missing
        - schema_version is not the expected value
        - run_id is empty or not a string
        """
        target = Path(path)
        if not target.exists():
            raise StateError(f"State file not found: {path}")

        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise StateError(f"Cannot read state file: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StateError(f"State file is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise StateError("State file must contain a JSON object at the top level")

        # SECGOV-R8 — check all required keys are present
        missing = _REQUIRED_KEYS - data.keys()
        if missing:
            raise StateError(f"State file missing required keys: {missing}")

        # SECGOV-R8 — version gate
        if data["schema_version"] != SCHEMA_VERSION:
            raise StateError(
                f"Incompatible schema_version: expected {SCHEMA_VERSION}, "
                f"got {data['schema_version']!r}"
            )

        run_id = data["run_id"]
        if not isinstance(run_id, str) or not run_id:
            raise StateError("State file run_id must be a non-empty string")

        phase = data["phase"]
        if not isinstance(phase, str):
            raise StateError("State file phase must be a string")

        completed = data["completed_actions"]
        if not isinstance(completed, list) or not all(isinstance(a, str) for a in completed):
            raise StateError("completed_actions must be a list of strings")

        side_effects = data["side_effects"]
        if not isinstance(side_effects, list) or not all(
            isinstance(k, str) for k in side_effects
        ):
            raise StateError("side_effects must be a list of strings")

        obj = cls(run_id=run_id, phase=phase)
        for action_id in completed:
            obj.mark_done(action_id)
        for key in side_effects:
            obj.record_side_effect(key)
        return obj

    @classmethod
    def resume_or_new(cls, path: str, run_id: str) -> "RunState":
        """Load existing checkpoint if run_id matches; otherwise return a fresh state.

        STATE-R1: A resumed run continues from the exact interrupted action.
                  A new run_id starts with empty state.
        """
        target = Path(path)
        if target.exists():
            try:
                existing = cls.load(path)
                if existing.run_id == run_id:
                    return existing
            except StateError:
                # Malformed file — treat as no checkpoint; start fresh
                pass
        return cls(run_id=run_id)
