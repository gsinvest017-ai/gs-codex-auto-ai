"""
termination.py — Three independent termination guards for CodexAutoAI v2.

Guards:
  ORCH-R2  MaxIterationsGuard  — configurable max iteration limit
  ORCH-R3  NoProgressGuard     — detects stuck loops via defect-set shrinkage
  ORCH-R4  BudgetGuard         — cumulative tokens / cost / wall-clock ceiling

  TerminationController combines all three and returns the first tripped reason.
"""

from __future__ import annotations

import hashlib
import time


# ---------------------------------------------------------------------------
# ORCH-R2 — Max iterations
# ---------------------------------------------------------------------------

class MaxIterationsGuard:
    """Trip when *iteration* reaches the configured limit.

    ``check(iteration)`` returns True (tripped) when ``iteration >= limit``.
    Iteration numbering is 0-based: with limit=3 the guard trips at 0,1,2 done
    and iteration==3 meaning "about to start the 4th pass".
    """

    def __init__(self, limit: int = 3) -> None:
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit!r}")
        self.limit = limit

    def check(self, iteration: int) -> bool:
        """Return True if *iteration* has met or exceeded the limit."""
        return iteration >= self.limit


# ---------------------------------------------------------------------------
# ORCH-R3 — No-progress (defect-set shrinkage)
# ---------------------------------------------------------------------------

def _defect_hash(defect_set) -> str:
    """Normalize *defect_set* (any iterable of str) and return a stable SHA-256 hex digest.

    Normalization: convert each element to str, deduplicate via set(), sort
    lexicographically, then hash the joined representation.
    """
    normalized = sorted({str(d) for d in defect_set})
    payload = "\n".join(normalized).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class NoProgressGuard:
    """Detect stuck loops by tracking whether the defect set shrinks each iteration.

    Rules:
    - Each call to ``update(defect_set)`` records the current defect count.
    - A "no-progress" event is recorded when the new count is *not strictly less*
      than the previous count (i.e. same or grew).
    - If the count *does* shrink, the consecutive-no-progress counter resets to 0.
    - When the consecutive-no-progress counter reaches *patience*, ``update``
      returns True (stuck).
    - The very first call is never counted as no-progress (there is no prior
      baseline to compare against).
    """

    def __init__(self, patience: int = 2) -> None:
        if patience < 1:
            raise ValueError(f"patience must be >= 1, got {patience!r}")
        self.patience = patience
        self._consecutive: int = 0
        self._prev_count: int | None = None
        self._prev_hash: str | None = None

    def update(self, defect_set) -> bool:
        """Record this iteration's defect set and return True if stuck."""
        items = list(defect_set)
        # Normalize: deduplicate before counting, consistent with _defect_hash.
        normalized_items = list({str(d) for d in items})
        current_count = len(normalized_items)
        current_hash = _defect_hash(items)

        if self._prev_count is None:
            # First call — establish baseline, never trip on first call.
            self._prev_count = current_count
            self._prev_hash = current_hash
            return False

        if current_count < self._prev_count:
            # Defect set shrank — real progress, reset counter.
            self._consecutive = 0
        else:
            # Same size or grew — no progress.
            self._consecutive += 1

        self._prev_count = current_count
        self._prev_hash = current_hash

        return self._consecutive >= self.patience

    def reset(self) -> None:
        """Manually reset all state (e.g. after a replan)."""
        self._consecutive = 0
        self._prev_count = None
        self._prev_hash = None


# ---------------------------------------------------------------------------
# ORCH-R4 — Budget
# ---------------------------------------------------------------------------

class BudgetGuard:
    """Track cumulative tokens, cost (USD), and wall-clock seconds.

    Any ceiling set to *None* means that dimension is uncapped.

    Usage::

        g = BudgetGuard(max_tokens=500_000, max_cost=10.0, max_seconds=3600)
        g.add(tokens=12000, cost=0.24)
        if g.check(elapsed_seconds=120.0):
            ...  # over budget
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        max_cost: float | None = None,
        max_seconds: float | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_cost = max_cost
        self.max_seconds = max_seconds

        self._tokens: int = 0
        self._cost: float = 0.0

    def add(self, tokens: int = 0, cost: float = 0.0) -> None:
        """Accumulate *tokens* and *cost* from one agent call."""
        if tokens < 0:
            raise ValueError(f"tokens must be >= 0, got {tokens!r}")
        if cost < 0.0:
            raise ValueError(f"cost must be >= 0.0, got {cost!r}")
        self._tokens += tokens
        self._cost += cost

    def check(self, elapsed_seconds: float = 0.0) -> bool:
        """Return True if any configured ceiling has been reached or exceeded."""
        if self.max_tokens is not None and self._tokens >= self.max_tokens:
            return True
        if self.max_cost is not None and self._cost >= self.max_cost:
            return True
        if self.max_seconds is not None and elapsed_seconds >= self.max_seconds:
            return True
        return False

    @property
    def tokens(self) -> int:
        return self._tokens

    @property
    def cost(self) -> float:
        return self._cost


# ---------------------------------------------------------------------------
# Combined controller
# ---------------------------------------------------------------------------

class TerminationController:
    """Combine all three guards; return the first tripped reason or None.

    ``step()`` evaluates ORCH-R2, ORCH-R3, ORCH-R4 in that order and returns
    the string label of the first guard that trips, or None if all pass.

    Reason strings: ``'max_iterations'`` | ``'no_progress'`` | ``'budget'``
    """

    def __init__(
        self,
        max_iterations: int = 3,
        patience: int = 2,
        max_tokens: int | None = None,
        max_cost: float | None = None,
        max_seconds: float | None = None,
    ) -> None:
        self._iter_guard = MaxIterationsGuard(limit=max_iterations)
        self._progress_guard = NoProgressGuard(patience=patience)
        self._budget_guard = BudgetGuard(
            max_tokens=max_tokens,
            max_cost=max_cost,
            max_seconds=max_seconds,
        )

    def step(
        self,
        iteration: int,
        defect_set,
        tokens: int = 0,
        cost: float = 0.0,
        elapsed_seconds: float = 0.0,
    ) -> str | None:
        """Evaluate all guards and return the first tripped reason, or None.

        Side-effects: updates the no-progress counter and accumulates
        tokens/cost in the budget guard.

        Parameters
        ----------
        iteration:
            Current (0-based) iteration index.
        defect_set:
            Iterable of defects reported by reviewer/tester this iteration.
        tokens:
            Tokens consumed this step (added to cumulative total).
        cost:
            Cost (USD) consumed this step.
        elapsed_seconds:
            Wall-clock time elapsed since run start.
        """
        # Accumulate budget first so check reflects this step's usage.
        self._budget_guard.add(tokens=tokens, cost=cost)

        # Evaluate in priority order: iterations → progress → budget.
        if self._iter_guard.check(iteration):
            return "max_iterations"

        if self._progress_guard.update(defect_set):
            return "no_progress"

        if self._budget_guard.check(elapsed_seconds=elapsed_seconds):
            return "budget"

        return None
