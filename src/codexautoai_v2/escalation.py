"""
ORCH-R5 Escalation — CodexAutoAI v2

When a termination guard trips, attempt EXACTLY ONE replan.
If the replan resolves the blocker, return None (resolved).
If not, enter a TERMINAL escalation state carrying diff + critique.
Must NOT loop back forever.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EscalationState:
    """Terminal record produced when a blocker cannot be resolved."""

    reason: str
    diff: str
    critique: str
    replanned: bool


class Escalator:
    """
    Handles guard-trip escalation with a guaranteed at-most-one replan
    guarantee.

    State:
        _replanned  — True once a replan attempt has been made.  Any
                      subsequent call skips the replan entirely.
    """

    def __init__(self) -> None:
        self._replanned: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle(
        self,
        reason: str,
        diff: str = "",
        critique: str = "",
        replan_fn=None,
    ) -> EscalationState | None:
        """
        Handle a guard-trip event.

        Parameters
        ----------
        reason:
            Human-readable description of the blocker.
        diff:
            Current diff at the time of the guard trip.
        critique:
            Unresolved critique text.
        replan_fn:
            Optional callable with no required arguments.  If provided
            and no replan has been attempted yet, it is called ONCE.
            If it returns a truthy value under the key ``'resolved'``
            (i.e. the return value itself is truthy and either is a
            mapping with ``result.get('resolved')`` truthy, or the
            callable returns ``True`` / any truthy non-mapping), the
            blocker is considered resolved and ``None`` is returned.

        Returns
        -------
        None
            The blocker was resolved by the single replan attempt.
        EscalationState
            Terminal state — no further replan will be attempted.
        """
        if replan_fn is not None and not self._replanned:
            self._replanned = True
            result = replan_fn()
            # Accept both plain truthy values and mapping with 'resolved' key.
            resolved = _extract_resolved(result)
            if resolved:
                return None
            # Replan ran but did not resolve — fall through to terminal state.
            return EscalationState(
                reason=reason,
                diff=diff,
                critique=critique,
                replanned=True,
            )

        # Either no replan_fn, or replan already exhausted.
        return EscalationState(
            reason=reason,
            diff=diff,
            critique=critique,
            replanned=self._replanned,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_resolved(result) -> bool:
    """Return True when *result* signals a successful resolution."""
    if result is None:
        return False
    if isinstance(result, dict):
        return bool(result.get("resolved"))
    return bool(result)
