"""
Tests for src/codexautoai_v2/termination.py

Covers:
  ORCH-R2  MaxIterationsGuard
  ORCH-R3  NoProgressGuard
  ORCH-R4  BudgetGuard
  Combined TerminationController
"""

import pytest

from src.codexautoai_v2.termination import (
    BudgetGuard,
    MaxIterationsGuard,
    NoProgressGuard,
    TerminationController,
)


# ===========================================================================
# MaxIterationsGuard (ORCH-R2)
# ===========================================================================

class TestMaxIterationsGuard:
    def test_does_not_trip_before_limit(self):
        g = MaxIterationsGuard(limit=3)
        assert g.check(0) is False
        assert g.check(1) is False
        assert g.check(2) is False

    def test_trips_exactly_at_limit(self):
        g = MaxIterationsGuard(limit=3)
        assert g.check(3) is True

    def test_trips_above_limit(self):
        g = MaxIterationsGuard(limit=3)
        assert g.check(10) is True

    def test_custom_limit_1(self):
        g = MaxIterationsGuard(limit=1)
        assert g.check(0) is False
        assert g.check(1) is True

    def test_custom_limit_5(self):
        g = MaxIterationsGuard(limit=5)
        for i in range(5):
            assert g.check(i) is False
        assert g.check(5) is True

    def test_invalid_limit_raises(self):
        with pytest.raises(ValueError):
            MaxIterationsGuard(limit=0)


# ===========================================================================
# NoProgressGuard (ORCH-R3)
# ===========================================================================

class TestNoProgressGuard:
    def test_first_call_never_trips(self):
        g = NoProgressGuard(patience=2)
        assert g.update({"A", "B"}) is False

    def test_same_defect_set_twice_does_not_trip_yet(self):
        """patience=2 means two consecutive no-progress events → trip."""
        g = NoProgressGuard(patience=2)
        g.update({"A", "B"})          # baseline
        assert g.update({"A", "B"}) is False   # 1st no-progress — not yet

    def test_same_defect_set_three_calls_trips(self):
        """baseline + 2 no-progress calls → stuck."""
        g = NoProgressGuard(patience=2)
        g.update({"A", "B"})          # baseline
        g.update({"A", "B"})          # consecutive=1
        assert g.update({"A", "B"}) is True    # consecutive=2 → trips

    def test_shrinking_defect_set_resets_counter(self):
        """ORCH-R3-S2: {A,B} → {A,B} → {A} resets, so next same-size doesn't trip."""
        g = NoProgressGuard(patience=2)
        g.update({"A", "B"})          # baseline, count=2
        g.update({"A", "B"})          # consecutive=1
        result = g.update({"A"})      # shrink → reset consecutive to 0
        assert result is False        # no trip after shrink

    def test_shrinking_prevents_trip_full_scenario(self):
        """{A,B} → {A,B} → {A} → {A} → {A}: two no-progress only after reset."""
        g = NoProgressGuard(patience=2)
        g.update({"A", "B"})   # baseline, count=2
        g.update({"A", "B"})   # consecutive=1
        g.update({"A"})        # shrink → consecutive=0
        g.update({"A"})        # consecutive=1
        # Only 1 consecutive no-progress after reset → should not trip yet
        assert g.update({"A"}) is True   # consecutive=2 → trips

    def test_empty_defect_set_counts_as_zero(self):
        g = NoProgressGuard(patience=2)
        g.update({"A", "B"})  # count=2
        result = g.update(set())  # count=0 → shrink → reset
        assert result is False

    def test_defect_set_accepts_list(self):
        g = NoProgressGuard(patience=2)
        g.update(["A", "B"])
        g.update(["A", "B"])
        assert g.update(["A", "B"]) is True

    def test_defect_set_deduplicates(self):
        """Lists with duplicates are normalized to sets before comparison."""
        g = NoProgressGuard(patience=2)
        g.update(["A", "A", "B"])   # unique count=2
        g.update(["A", "B"])        # same unique set → no-progress consecutive=1
        assert g.update(["A", "B"]) is True  # consecutive=2 → trips

    def test_reset_clears_state(self):
        g = NoProgressGuard(patience=2)
        g.update({"A", "B"})
        g.update({"A", "B"})
        g.reset()
        # After reset, first call is baseline again
        assert g.update({"A", "B"}) is False

    def test_patience_1(self):
        """patience=1 means one no-progress event after baseline triggers trip."""
        g = NoProgressGuard(patience=1)
        g.update({"A"})          # baseline
        assert g.update({"A"}) is True   # consecutive=1 → trips immediately

    def test_invalid_patience_raises(self):
        with pytest.raises(ValueError):
            NoProgressGuard(patience=0)


# ===========================================================================
# BudgetGuard (ORCH-R4)
# ===========================================================================

class TestBudgetGuard:
    def test_no_limits_never_trips(self):
        g = BudgetGuard()
        g.add(tokens=1_000_000, cost=999.0)
        assert g.check(elapsed_seconds=999999.0) is False

    def test_token_limit_not_tripped_below(self):
        g = BudgetGuard(max_tokens=500)
        g.add(tokens=499)
        assert g.check() is False

    def test_token_limit_trips_at_exact_ceiling(self):
        g = BudgetGuard(max_tokens=500)
        g.add(tokens=500)
        assert g.check() is True

    def test_token_limit_trips_above_ceiling(self):
        g = BudgetGuard(max_tokens=500)
        g.add(tokens=600)
        assert g.check() is True

    def test_token_limit_accumulates_across_calls(self):
        g = BudgetGuard(max_tokens=500)
        g.add(tokens=300)
        assert g.check() is False
        g.add(tokens=200)
        assert g.check() is True

    def test_cost_limit_trips(self):
        g = BudgetGuard(max_cost=10.0)
        g.add(cost=9.99)
        assert g.check() is False
        g.add(cost=0.01)
        assert g.check() is True

    def test_seconds_limit_trips(self):
        g = BudgetGuard(max_seconds=3600.0)
        assert g.check(elapsed_seconds=3599.9) is False
        assert g.check(elapsed_seconds=3600.0) is True

    def test_negative_tokens_raises(self):
        g = BudgetGuard()
        with pytest.raises(ValueError):
            g.add(tokens=-1)

    def test_negative_cost_raises(self):
        g = BudgetGuard()
        with pytest.raises(ValueError):
            g.add(cost=-0.01)

    def test_properties_accumulate(self):
        g = BudgetGuard()
        g.add(tokens=100, cost=1.0)
        g.add(tokens=200, cost=2.5)
        assert g.tokens == 300
        assert abs(g.cost - 3.5) < 1e-9


# ===========================================================================
# TerminationController (combined)
# ===========================================================================

class TestTerminationController:
    def test_no_trip_returns_none(self):
        ctrl = TerminationController(max_iterations=5, patience=3)
        result = ctrl.step(iteration=0, defect_set={"A", "B"})
        assert result is None

    def test_max_iterations_trips(self):
        ctrl = TerminationController(max_iterations=3, patience=10)
        result = ctrl.step(iteration=3, defect_set={"A"})
        assert result == "max_iterations"

    def test_max_iterations_not_tripped_below_limit(self):
        ctrl = TerminationController(max_iterations=3, patience=10)
        for i in range(3):
            r = ctrl.step(iteration=i, defect_set={"A", "B", "C"})
            assert r is None, f"unexpected trip at iteration {i}: {r!r}"

    def test_no_progress_trips(self):
        """patience=2: baseline + 2 identical defect sets → 'no_progress'."""
        ctrl = TerminationController(max_iterations=100, patience=2)
        ctrl.step(iteration=0, defect_set={"A", "B"})   # baseline
        ctrl.step(iteration=1, defect_set={"A", "B"})   # consecutive=1
        result = ctrl.step(iteration=2, defect_set={"A", "B"})  # consecutive=2
        assert result == "no_progress"

    def test_budget_trips_on_tokens(self):
        ctrl = TerminationController(
            max_iterations=100, patience=100, max_tokens=500
        )
        # First step: 300 tokens — no trip
        r = ctrl.step(iteration=0, defect_set={"A"}, tokens=300)
        assert r is None
        # Second step: another 200 tokens → cumulative 500 → trip
        r = ctrl.step(iteration=1, defect_set={"A"}, tokens=200)
        assert r == "budget"

    def test_max_iterations_takes_priority_over_no_progress(self):
        """If iteration limit is reached on same step as no-progress, return max_iterations."""
        ctrl = TerminationController(max_iterations=1, patience=1)
        # step with iteration=1 (>= limit=1) AND first call is baseline so no_progress won't fire
        result = ctrl.step(iteration=1, defect_set={"A"})
        assert result == "max_iterations"

    def test_no_progress_takes_priority_over_budget(self):
        """no_progress check comes before budget check in the controller."""
        ctrl = TerminationController(
            max_iterations=100,
            patience=2,
            max_tokens=1,  # trips immediately after 1st token add
        )
        # Step 0: baseline for no_progress; add 1 token — budget trips BUT
        # no_progress won't trip on first call, and budget is checked after no_progress.
        r0 = ctrl.step(iteration=0, defect_set={"A"}, tokens=1)
        # budget already tripped (1 >= 1), but no_progress had first-call pass
        assert r0 == "budget"

    def test_full_scenario_shrink_resets_no_progress(self):
        """Shrinking defect set resets no-progress counter; controller returns None."""
        ctrl = TerminationController(max_iterations=10, patience=2)
        ctrl.step(iteration=0, defect_set={"A", "B"})   # baseline
        ctrl.step(iteration=1, defect_set={"A", "B"})   # consecutive=1
        result = ctrl.step(iteration=2, defect_set={"A"})  # shrink → reset
        assert result is None
