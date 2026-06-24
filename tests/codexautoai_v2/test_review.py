"""
Tests for src/codexautoai_v2/review.py

Covers:
  REVIEW-R1 — select_reviewer_model (cross-model + single-model fallback)
  REVIEW-R2 — should_skip_llm_review + require_grounding
"""

import pytest

from src.codexautoai_v2.review import (
    GroundingSignals,
    ReviewError,
    require_grounding,
    select_reviewer_model,
    should_skip_llm_review,
)


# ---------------------------------------------------------------------------
# select_reviewer_model — REVIEW-R1
# ---------------------------------------------------------------------------

class TestSelectReviewerModel:
    def test_picks_different_model_when_available(self):
        result = select_reviewer_model("codex", ["codex", "claude"])
        assert result["reviewer"] == "claude"
        assert result["independent"] is True

    def test_independent_false_when_only_one_model(self):
        result = select_reviewer_model("codex", ["codex"])
        assert result["reviewer"] == "codex"
        assert result["independent"] is False

    def test_picks_first_non_fixer_model(self):
        result = select_reviewer_model("codex", ["codex", "gpt4", "claude"])
        assert result["reviewer"] in ("gpt4", "claude")
        assert result["independent"] is True

    def test_picks_non_fixer_when_fixer_not_first(self):
        result = select_reviewer_model("claude", ["codex", "claude"])
        assert result["reviewer"] == "codex"
        assert result["independent"] is True

    def test_empty_list_returns_fixer_non_independent(self):
        result = select_reviewer_model("codex", [])
        assert result["reviewer"] == "codex"
        assert result["independent"] is False

    def test_returns_dict_with_required_keys(self):
        result = select_reviewer_model("codex", ["codex", "claude"])
        assert "reviewer" in result
        assert "independent" in result


# ---------------------------------------------------------------------------
# should_skip_llm_review — REVIEW-R2
# ---------------------------------------------------------------------------

class TestShouldSkipLlmReview:
    def test_skip_when_compiled_false(self):
        signals = GroundingSignals(compiled=False, test_output="", lint_output="")
        assert should_skip_llm_review(signals) is True

    def test_no_skip_when_compiled_true(self):
        signals = GroundingSignals(
            compiled=True,
            test_output="1 passed",
            lint_output="",
        )
        assert should_skip_llm_review(signals) is False

    def test_skip_compiled_false_regardless_of_outputs(self):
        signals = GroundingSignals(
            compiled=False,
            test_output="3 passed",
            lint_output="no issues",
        )
        assert should_skip_llm_review(signals) is True


# ---------------------------------------------------------------------------
# require_grounding — REVIEW-R2
# ---------------------------------------------------------------------------

class TestRequireGrounding:
    def test_raises_when_signals_is_none(self):
        with pytest.raises(ReviewError):
            require_grounding(None)

    def test_does_not_raise_with_valid_signals(self):
        signals = GroundingSignals(
            compiled=True,
            test_output="5 passed, 0 failed",
            lint_output="no lint errors",
        )
        require_grounding(signals)  # must not raise

    def test_does_not_raise_when_compiled_false_but_signals_present(self):
        signals = GroundingSignals(
            compiled=False,
            test_output="",
            lint_output="",
        )
        require_grounding(signals)  # compiled=False is still a grounding signal

    def test_does_not_raise_with_minimal_signals(self):
        signals = GroundingSignals(compiled=True, test_output="", lint_output="")
        require_grounding(signals)  # compiled is grounding info

    def test_error_message_mentions_grounding(self):
        with pytest.raises(ReviewError, match="grounding"):
            require_grounding(None)
