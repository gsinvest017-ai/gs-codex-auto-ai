"""
property_verifier.py — Kiro property-based verification for CodexAutoAI v2.

Implements REVIEW-R3: compile each requirement's EARS acceptance scenarios into
runnable checks, execute AFTER build and BEFORE delivery; block if any fail.

Parses OpenSpec-style GIVEN/WHEN/THEN scenarios from Markdown, runs user-supplied
check callables, and produces a VerificationReport.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Property:
    """Structured representation of a single EARS acceptance scenario."""

    scenario_id: str
    given: list[str]
    when: list[str]
    then: list[str]


@dataclass
class VerificationReport:
    """Result of running property checks against a set of Properties.

    ok is True only when there are no failures AND no skipped properties
    (i.e. every property had a runnable check and all passed).
    """

    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.failed) == 0 and len(self.skipped) == 0


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Matches scenario headings like:
#   #### Scenario: ORCH-R2-S1 — name
#   ### Scenario: ORCH-R2-S1 - name
_SCENARIO_HEADING = re.compile(
    r"^#{1,6}\s+Scenario:\s+(\S+)",
    re.MULTILINE,
)

# Matches a bullet line like:  - GIVEN some text
_BULLET = re.compile(r"^\s*-\s+(GIVEN|WHEN|THEN)\s+(.*)", re.IGNORECASE)


def parse_scenarios(markdown_text: str) -> list[Property]:
    """Parse all EARS scenarios from a Markdown string.

    Recognises headings of the form::

        #### Scenario: SCENARIO-ID — optional title

    followed by bullet lines::

        - GIVEN ...
        - WHEN  ...
        - THEN  ...

    Returns a list of Property objects in document order.
    Any scenario heading with no subsequent GIVEN/WHEN/THEN bullets is still
    returned (with empty lists), because the caller may want to know it exists.
    """
    properties: list[Property] = []

    # Split on scenario headings, keeping the delimiters so we can extract ids.
    # Strategy: find all heading positions, then extract bullets from each block.
    lines = markdown_text.splitlines()

    current_id: str | None = None
    current_given: list[str] = []
    current_when: list[str] = []
    current_then: list[str] = []
    in_scenario: bool = False

    def _flush() -> None:
        if current_id is not None:
            properties.append(
                Property(
                    scenario_id=current_id,
                    given=list(current_given),
                    when=list(current_when),
                    then=list(current_then),
                )
            )

    for line in lines:
        heading_match = _SCENARIO_HEADING.match(line)
        if heading_match:
            # Save the previous scenario before starting a new one.
            _flush()
            current_id = heading_match.group(1)
            current_given = []
            current_when = []
            current_then = []
            in_scenario = True
            continue

        if not in_scenario:
            continue

        bullet_match = _BULLET.match(line)
        if bullet_match:
            tag = bullet_match.group(1).upper()
            text = bullet_match.group(2).strip()
            if tag == "GIVEN":
                current_given.append(text)
            elif tag == "WHEN":
                current_when.append(text)
            elif tag == "THEN":
                current_then.append(text)

    # Flush the last scenario.
    _flush()

    return properties


# ---------------------------------------------------------------------------
# Verification runner
# ---------------------------------------------------------------------------

def verify(
    properties: list[Property],
    checks: dict[str, object],
) -> VerificationReport:
    """Run user-supplied checks against a list of Property objects.

    For each property:
    - If ``checks`` contains the ``scenario_id``, call it with no arguments.
      - Truthy return value  -> passed
      - Falsy return value   -> failed
      - Any exception raised -> failed
    - If no check is provided for a scenario_id -> skipped.

    ``ok`` is True only when there are zero failures AND zero skips.
    """
    report = VerificationReport()

    for prop in properties:
        sid = prop.scenario_id
        if sid not in checks:
            report.skipped.append(sid)
            continue

        check = checks[sid]
        try:
            result = check()  # type: ignore[operator]
            if result:
                report.passed.append(sid)
            else:
                report.failed.append(sid)
        except Exception:
            report.failed.append(sid)

    return report


# ---------------------------------------------------------------------------
# Stub generator
# ---------------------------------------------------------------------------

def safe_name(scenario_id: str) -> str:
    r"""把 scenario id 變成合法的 Python 識別碼片段，**保留 CJK**。

    以前是 ``[^A-Za-z0-9] -> _``，中文場景名整段被吃掉：
    「情境：新增支出」與「情境：刪除支出」都變成同一個 ``______``，
    產出的檔案裡後面的 def 靜默覆蓋前面的，測試數量無聲蒸發
    （實測 23 個場景只剩 9 個唯一名字，14 個被吃掉）。

    Python 3 的識別碼本來就吃 CJK（PEP 3131），所以改用 unicode 的 ``\W``：
    只把真正不合法的字元換成底線。全部落空時退回 hash，寧可醜也不要撞名。
    """
    name = re.sub(r"\W", "_", scenario_id, flags=re.UNICODE)
    if not name.strip("_"):
        return "s_" + hashlib.sha1(scenario_id.encode("utf-8")).hexdigest()[:8]
    return name


def unique_names(scenario_ids: Sequence[str]) -> list[str]:
    """保證整批名字互不相同——撞到就加 ``_2`` / ``_3``。

    ``safe_name`` 讓撞名變罕見，但擋不住兩個場景真的同名（或只差在被正規化掉
    的字元）。靜默覆蓋是這個 bug 最貴的部分，所以這裡機械性地把它變成不可能。
    """
    used: dict[str, int] = {}
    out: list[str] = []
    for sid in scenario_ids:
        base = safe_name(sid)
        n = used.get(base, 0) + 1
        used[base] = n
        out.append(base if n == 1 else f"{base}_{n}")
    return out


def stub_for(prop: Property, name: str | None = None) -> str:
    """Return a pytest test function source string for a Property.

    The generated function is named ``test_<sanitized_scenario_id>`` where
    the scenario id has non-alphanumeric characters replaced with underscores.
    GIVEN/WHEN/THEN clauses appear as inline comments, and the body contains
    ``assert False  # TODO`` so the stub is immediately runnable (and failing)
    until the author fills it in.

    Example output::

        def test_ORCH_R2_S1():
            # GIVEN max_iterations = 3
            # WHEN the 3rd fix still fails
            # THEN the loop stops and escalates
            assert False  # TODO
    """
    lines: list[str] = [f"def test_{name or safe_name(prop.scenario_id)}():"]

    for text in prop.given:
        lines.append(f"    # GIVEN {text}")
    for text in prop.when:
        lines.append(f"    # WHEN {text}")
    for text in prop.then:
        lines.append(f"    # THEN {text}")

    lines.append("    assert False  # TODO")

    return "\n".join(lines) + "\n"


def stubs_for(props: Sequence[Property]) -> list[str]:
    """整批產生 stub，名字保證唯一。``gen-tests`` 一律走這條，不要逐個 stub_for。"""
    names = unique_names([p.scenario_id for p in props])
    return [stub_for(p, n) for p, n in zip(props, names)]
