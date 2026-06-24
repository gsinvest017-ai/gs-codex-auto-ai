"""Tests for src/codexautoai_v2/spec_authoring.py

Run with:
    .venv/Scripts/python -m pytest tests/codexautoai_v2/test_spec_authoring.py -q
"""
import pytest
from src.codexautoai_v2.spec_authoring import (
    SpecError,
    validate_spec,
    confirm_project_type,
    KNOWN_PROJECT_TYPES,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

VALID_SPEC = """\
## ADDED Requirements

### Requirement: ORCH-R1 — orchestration entry
THE SYSTEM SHALL coordinate all phases automatically.

#### Scenario: ORCH-R1-S1 — happy path
- GIVEN a valid project request
- WHEN the orchestrator starts
- THEN it SHALL delegate to phase agents in order
"""

MISSING_SCENARIO_SPEC = """\
## ADDED Requirements

### Requirement: ORCH-R2 — missing scenario
THE SYSTEM SHALL do something important.
"""

MISSING_NORMATIVE_SPEC = """\
## ADDED Requirements

### Requirement: ORCH-R3 — missing normative
The system does something (no normative keyword here).

#### Scenario: ORCH-R3-S1 — scenario present but requirement weak
- GIVEN some context
- WHEN action happens
- THEN outcome occurs
"""

MIXED_SPEC = """\
## ADDED Requirements

### Requirement: GOOD-R1 — valid requirement
THE SYSTEM SHALL do something good.

#### Scenario: GOOD-R1-S1 — passes validation
- GIVEN everything is correct
- WHEN validation runs
- THEN no errors SHALL be raised

### Requirement: BAD-R1 — bad requirement
The system does something without normative or scenario.
"""


# ---------------------------------------------------------------------------
# validate_spec tests
# ---------------------------------------------------------------------------

class TestValidateSpec:

    def test_valid_spec_returns_empty_list(self):
        """A requirement with SHALL and at least one scenario is valid."""
        errors = validate_spec(VALID_SPEC)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_missing_scenario_returns_one_error(self):
        """A requirement with SHALL but no scenario produces exactly one error."""
        errors = validate_spec(MISSING_SCENARIO_SPEC)
        assert len(errors) == 1
        err = errors[0]
        assert isinstance(err, SpecError)
        assert "ORCH-R2" in err.requirement
        assert "scenario" in err.message.lower()

    def test_missing_normative_keyword_returns_one_error(self):
        """A requirement with a scenario but no SHALL/MUST produces exactly one error."""
        errors = validate_spec(MISSING_NORMATIVE_SPEC)
        assert len(errors) == 1
        err = errors[0]
        assert isinstance(err, SpecError)
        assert "ORCH-R3" in err.requirement
        assert any(kw in err.message for kw in ("SHALL", "MUST", "normative"))

    def test_mixed_spec_only_flags_bad_requirement(self):
        """With one good and one bad requirement, only the bad one is flagged."""
        errors = validate_spec(MIXED_SPEC)
        # BAD-R1 has no normative keyword AND no scenario → 2 errors
        req_ids = [e.requirement for e in errors]
        assert "BAD-R1" in req_ids, f"BAD-R1 not flagged; errors: {errors}"
        assert not any("GOOD-R1" in r for r in req_ids), (
            f"GOOD-R1 was incorrectly flagged; errors: {errors}"
        )

    def test_empty_markdown_returns_empty_list(self):
        """Empty input has no requirements to validate."""
        assert validate_spec("") == []

    def test_must_keyword_is_also_accepted(self):
        """MUST is an accepted normative keyword (not just SHALL)."""
        spec = """\
## ADDED Requirements

### Requirement: MUST-R1 — uses MUST keyword
The system MUST handle failures gracefully.

#### Scenario: MUST-R1-S1 — failure handled
- GIVEN a failure occurs
- WHEN the system detects it
- THEN it MUST recover or alert
"""
        errors = validate_spec(spec)
        assert errors == [], f"Expected no errors for MUST keyword; got: {errors}"

    def test_multiple_scenarios_allowed(self):
        """A requirement with multiple scenarios still passes."""
        spec = """\
## ADDED Requirements

### Requirement: MULTI-R1 — multiple scenarios
THE SYSTEM SHALL support many flows.

#### Scenario: MULTI-R1-S1 — first path
- GIVEN path one
- WHEN triggered
- THEN result one SHALL occur

#### Scenario: MULTI-R1-S2 — second path
- GIVEN path two
- WHEN triggered
- THEN result two SHALL occur
"""
        errors = validate_spec(spec)
        assert errors == []


# ---------------------------------------------------------------------------
# confirm_project_type tests
# ---------------------------------------------------------------------------

class TestConfirmProjectType:

    def test_known_type_is_valid(self):
        result = confirm_project_type("quant-finance")
        assert result["valid"] is True

    def test_known_type_needs_confirmation(self):
        result = confirm_project_type("quant-finance")
        assert result["needs_confirmation"] is True

    def test_known_type_preserves_type_field(self):
        result = confirm_project_type("quant-finance")
        assert result["type"] == "quant-finance"

    def test_unknown_type_is_invalid(self):
        result = confirm_project_type("webapp")
        assert result["valid"] is False

    def test_unknown_type_still_needs_confirmation(self):
        """Even invalid types surface for human review (v2 always-confirm policy)."""
        result = confirm_project_type("webapp")
        assert result["needs_confirmation"] is True

    def test_all_known_types_are_valid(self):
        for ptype in KNOWN_PROJECT_TYPES:
            result = confirm_project_type(ptype)
            assert result["valid"] is True, f"Expected valid for {ptype!r}"

    @pytest.mark.parametrize("ptype", [
        "web-fullstack", "web-backend", "web-frontend",
        "data-science", "quant-finance", "ml-ai",
        "cli-tool", "library", "desktop-gui", "automation", "other",
    ])
    def test_each_known_type_parametrized(self, ptype):
        result = confirm_project_type(ptype)
        assert result["valid"] is True
        assert result["needs_confirmation"] is True
        assert result["type"] == ptype

    def test_empty_string_is_invalid(self):
        result = confirm_project_type("")
        assert result["valid"] is False

    def test_return_structure_has_required_keys(self):
        result = confirm_project_type("ml-ai")
        assert set(result.keys()) == {"valid", "needs_confirmation", "type"}
