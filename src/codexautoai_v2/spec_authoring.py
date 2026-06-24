"""spec_authoring.py — EARS requirement validation and project-type confirmation gate.

Implements AUTHOR-R1 (EARS + mandatory scenario) and AUTHOR-R2 (project-type
confirmation gate) from the spec-authoring OpenSpec delta.

Pure stdlib only (re).  No sibling v2 imports.  Windows-safe; no hardcoded paths.
"""
import re
from dataclasses import dataclass
from typing import List


# ---------------------------------------------------------------------------
# Public API — SpecError
# ---------------------------------------------------------------------------

@dataclass
class SpecError:
    """A single validation error tied to one requirement."""
    requirement: str   # requirement id, e.g. "ORCH-R1"
    message: str


# ---------------------------------------------------------------------------
# Internal regex helpers
# ---------------------------------------------------------------------------

# Matches an H3 requirement heading: ### Requirement: <id> — …
_REQ_HEADING = re.compile(
    r"^#{3}\s+Requirement:\s+(\S+)",   # group 1 = full id token (e.g. ORCH-R1)
    re.MULTILINE,
)

# Matches an H4 scenario heading: #### Scenario: …
_SCENARIO_HEADING = re.compile(
    r"^#{4}\s+Scenario:",
    re.MULTILINE,
)

# Normative keyword: SHALL or MUST (whole-word, case-sensitive per EARS convention)
_NORMATIVE = re.compile(r"\b(?:SHALL|MUST)\b")


# ---------------------------------------------------------------------------
# validate_spec
# ---------------------------------------------------------------------------

def validate_spec(markdown_text: str) -> List[SpecError]:
    """Parse *markdown_text* and return a list of SpecError for every
    requirement that violates AUTHOR-R1.

    A requirement is invalid when:
      (a) its body contains no SHALL or MUST  — normative keyword missing
      (b) there is no ``#### Scenario:`` heading before the next requirement

    An empty list means the spec is valid.
    """
    errors: List[SpecError] = []

    # Collect all requirement heading positions
    req_matches = list(_REQ_HEADING.finditer(markdown_text))
    if not req_matches:
        return errors

    for idx, match in enumerate(req_matches):
        req_id = match.group(1)

        # Determine the text body for this requirement:
        # from the end of this heading to the start of the *next* requirement
        # heading (or end of document).
        body_start = match.end()
        body_end = req_matches[idx + 1].start() if idx + 1 < len(req_matches) else len(markdown_text)
        body = markdown_text[body_start:body_end]

        # (a) check for normative keyword
        if not _NORMATIVE.search(body):
            errors.append(SpecError(
                requirement=req_id,
                message=(
                    f"Requirement {req_id} has no normative keyword (SHALL or MUST). "
                    "EARS syntax requires at least one."
                ),
            ))

        # (b) check for at least one scenario
        if not _SCENARIO_HEADING.search(body):
            errors.append(SpecError(
                requirement=req_id,
                message=(
                    f"Requirement {req_id} has no scenario. "
                    "At least one '#### Scenario:' block is required."
                ),
            ))

    return errors


# ---------------------------------------------------------------------------
# AUTHOR-R2 — project-type confirmation gate
# ---------------------------------------------------------------------------

KNOWN_PROJECT_TYPES = {
    "web-fullstack",
    "web-backend",
    "web-frontend",
    "data-science",
    "quant-finance",
    "ml-ai",
    "cli-tool",
    "library",
    "desktop-gui",
    "automation",
    "other",
}


def confirm_project_type(ptype: str) -> dict:
    """Return a confirmation gate dict for the classified project type.

    Always sets ``needs_confirmation=True`` (v2 policy: every classification
    surfaces for human review because a wrong type cascades into wrong
    architecture, tests, and report).

    Returns::

        {
            'valid': bool,            # True if ptype is in KNOWN_PROJECT_TYPES
            'needs_confirmation': True,
            'type': ptype,
        }
    """
    return {
        "valid": ptype in KNOWN_PROJECT_TYPES,
        "needs_confirmation": True,
        "type": ptype,
    }
