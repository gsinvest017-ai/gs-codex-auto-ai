"""
Tests for ORCH-R5 Escalation (escalation.py).

Coverage:
- replan called exactly once
- replan resolves blocker -> returns None
- replan does not resolve -> returns terminal EscalationState
- second call on same Escalator does not replan again
- no replan_fn -> immediate terminal state
- EscalationState fields populated correctly
"""
import pytest

from src.codexautoai_v2.escalation import Escalator, EscalationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _counter_replan(resolves: bool):
    """Return a replan callable that counts invocations."""
    calls = []

    def replan():
        calls.append(1)
        return {"resolved": resolves}

    return replan, calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEscalatorReplanCalledOnce:
    def test_replan_called_exactly_once_when_resolves(self):
        esc = Escalator()
        replan, calls = _counter_replan(resolves=True)

        result = esc.handle("guard tripped", diff="d1", critique="c1", replan_fn=replan)

        assert len(calls) == 1, "replan must be called exactly once"
        assert result is None, "resolved replan should return None"

    def test_replan_called_exactly_once_when_not_resolves(self):
        esc = Escalator()
        replan, calls = _counter_replan(resolves=False)

        result = esc.handle("guard tripped", diff="d1", critique="c1", replan_fn=replan)

        assert len(calls) == 1, "replan must be called exactly once"
        assert result is not None, "unresolved replan should return EscalationState"


class TestEscalatorResolvesCase:
    def test_returns_none_when_replan_resolves(self):
        esc = Escalator()
        replan, _ = _counter_replan(resolves=True)

        result = esc.handle("reason", diff="patch", critique="bad", replan_fn=replan)

        assert result is None

    def test_truthy_non_dict_also_resolves(self):
        esc = Escalator()

        result = esc.handle("r", replan_fn=lambda: True)

        assert result is None

    def test_plain_true_resolves(self):
        esc = Escalator()
        result = esc.handle("r", replan_fn=lambda: 1)
        assert result is None


class TestEscalatorTerminalCase:
    def test_returns_escalation_state_when_replan_fails(self):
        esc = Escalator()
        replan, _ = _counter_replan(resolves=False)

        state = esc.handle("blocker", diff="diff1", critique="crit1", replan_fn=replan)

        assert isinstance(state, EscalationState)
        assert state.reason == "blocker"
        assert state.diff == "diff1"
        assert state.critique == "crit1"
        assert state.replanned is True

    def test_terminal_state_when_no_replan_fn(self):
        esc = Escalator()

        state = esc.handle("no replan", diff="d", critique="c")

        assert isinstance(state, EscalationState)
        assert state.reason == "no replan"
        assert state.diff == "d"
        assert state.critique == "c"
        assert state.replanned is False

    def test_false_dict_resolved_is_terminal(self):
        esc = Escalator()

        state = esc.handle("r", replan_fn=lambda: {"resolved": False})

        assert isinstance(state, EscalationState)

    def test_empty_dict_is_terminal(self):
        esc = Escalator()
        state = esc.handle("r", replan_fn=lambda: {})
        assert isinstance(state, EscalationState)

    def test_none_return_from_replan_is_terminal(self):
        esc = Escalator()
        state = esc.handle("r", replan_fn=lambda: None)
        assert isinstance(state, EscalationState)


class TestEscalatorNoRepeatReplan:
    def test_second_call_does_not_replan(self):
        esc = Escalator()
        replan, calls = _counter_replan(resolves=False)

        # First call — replan runs.
        esc.handle("first", replan_fn=replan)
        assert len(calls) == 1

        # Second call on same Escalator — replan must NOT run again.
        state = esc.handle("second", diff="d2", critique="c2", replan_fn=replan)
        assert len(calls) == 1, "replan must not be called a second time"
        assert isinstance(state, EscalationState)
        assert state.replanned is True  # flag carried from prior attempt

    def test_second_call_without_replan_fn_also_terminal(self):
        esc = Escalator()
        replan, calls = _counter_replan(resolves=False)

        esc.handle("first", replan_fn=replan)
        state = esc.handle("second")  # no replan_fn

        assert isinstance(state, EscalationState)
        assert len(calls) == 1

    def test_resolved_first_call_then_second_call_is_terminal_without_new_replan(self):
        """
        Even if the first call resolved (returned None), the Escalator
        has used its one replan slot.  A second guard trip should be
        terminal immediately.
        """
        esc = Escalator()
        replan, calls = _counter_replan(resolves=True)

        first = esc.handle("first", replan_fn=replan)
        assert first is None
        assert len(calls) == 1

        second = esc.handle("second", replan_fn=replan)
        assert isinstance(second, EscalationState)
        assert len(calls) == 1, "replan must not be called again after it already ran"


class TestEscalationStateFields:
    def test_default_diff_and_critique_are_empty_strings(self):
        esc = Escalator()
        state = esc.handle("minimal")
        assert state.diff == ""
        assert state.critique == ""

    def test_replanned_false_with_no_replan_fn(self):
        esc = Escalator()
        state = esc.handle("r")
        assert state.replanned is False
