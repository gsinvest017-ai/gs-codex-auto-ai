"""CodexAutoAI v2 — REVIEW-R4 / SECGOV-R3: secret & SAST scanner.

Scans generated source code for hardcoded secrets and basic Python SAST issues.
A HIGH-severity finding blocks delivery (gates pipeline progression).

Stdlib only: re, dataclasses, enum.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Severity constants
# ---------------------------------------------------------------------------

class Severity:
    """String-constant severity levels (not an Enum so callers use plain strings)."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    rule: str
    severity: str
    line: int
    message: str


# ---------------------------------------------------------------------------
# Secret detection rules
# ---------------------------------------------------------------------------

# Each rule: (rule_name, severity, compiled_pattern, message_template)
# Patterns are matched per-line; `{match}` in message is replaced with the
# first group (or full match) so we never log the literal secret.

_SECRET_RULES: list[tuple[str, str, re.Pattern[str], str]] = [
    (
        "OPENAI_API_KEY",
        Severity.HIGH,
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        "Hardcoded OpenAI-style API key detected",
    ),
    (
        "AWS_ACCESS_KEY",
        Severity.HIGH,
        re.compile(r"AKIA[0-9A-Z]{16}"),
        "Hardcoded AWS access key ID detected",
    ),
    (
        "HARDCODED_CREDENTIAL",
        Severity.HIGH,
        re.compile(
            r"""(?:password|passwd|secret|api_key)\s*=\s*["'][^"']{1,}["']""",
            re.IGNORECASE,
        ),
        "Hardcoded credential assignment detected",
    ),
    (
        "BEARER_TOKEN",
        Severity.MEDIUM,
        re.compile(r"""['"]?Bearer\s+[A-Za-z0-9\-_\.~\+\/]+=*['"]?"""),
        "Hardcoded Bearer token detected",
    ),
]

# ---------------------------------------------------------------------------
# SAST detection rules
# ---------------------------------------------------------------------------

_SAST_RULES: list[tuple[str, str, re.Pattern[str], str]] = [
    (
        "EVAL_USAGE",
        Severity.HIGH,
        re.compile(r"""\beval\s*\("""),
        "Use of eval() is dangerous and may allow code injection",
    ),
    (
        "EXEC_USAGE",
        Severity.HIGH,
        re.compile(r"""\bexec\s*\("""),
        "Use of exec() is dangerous and may allow code injection",
    ),
    (
        "SUBPROCESS_SHELL_TRUE",
        Severity.HIGH,
        re.compile(r"""subprocess\s*\.\s*\w+\s*\(.*?shell\s*=\s*True"""),
        "subprocess called with shell=True — command injection risk",
    ),
    (
        "PICKLE_LOADS",
        Severity.MEDIUM,
        re.compile(r"""\bpickle\s*\.\s*loads\s*\("""),
        "pickle.loads() deserialises arbitrary data — deserialization attack risk",
    ),
    (
        "REQUESTS_VERIFY_FALSE",
        Severity.MEDIUM,
        re.compile(r"""verify\s*=\s*False"""),
        "SSL certificate verification disabled (verify=False)",
    ),
    (
        "YAML_LOAD_UNSAFE",
        Severity.MEDIUM,
        re.compile(r"""\byaml\s*\.\s*load\s*\("""),
        "yaml.load() without SafeLoader may execute arbitrary code; use yaml.safe_load()",
    ),
]


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _apply_rules(
    source: str,
    rules: list[tuple[str, str, re.Pattern[str], str]],
) -> list[Finding]:
    """Apply a list of (name, severity, pattern, message) rules line-by-line."""
    findings: list[Finding] = []
    lines = source.splitlines()
    for lineno, line in enumerate(lines, start=1):
        for rule_name, severity, pattern, message in rules:
            if pattern.search(line):
                findings.append(
                    Finding(
                        rule=rule_name,
                        severity=severity,
                        line=lineno,
                        message=message,
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_secrets(source: str) -> list[Finding]:
    """Scan *source* for hardcoded secrets.

    Detects:
    - OpenAI-style key  ``sk-[A-Za-z0-9]{20,}``          → HIGH
    - AWS access key ID ``AKIA[0-9A-Z]{16}``              → HIGH
    - Credential assignment (password/passwd/secret/api_key = "…") → HIGH
    - Hardcoded Bearer token                               → MEDIUM
    """
    return _apply_rules(source, _SECRET_RULES)


def scan_sast(source: str) -> list[Finding]:
    """Scan *source* for basic Python SAST anti-patterns.

    Detects:
    - ``eval(``                         → HIGH
    - ``exec(``                         → HIGH
    - ``subprocess(…, shell=True)``     → HIGH
    - ``pickle.loads(``                 → MEDIUM
    - ``verify=False`` in requests      → MEDIUM
    - ``yaml.load(`` without SafeLoader → MEDIUM
    """
    return _apply_rules(source, _SAST_RULES)


def scan(source: str) -> list[Finding]:
    """Run both secret and SAST scans and return combined findings."""
    return scan_secrets(source) + scan_sast(source)


def blocks_delivery(findings: list[Finding]) -> bool:
    """Return True if any finding has HIGH severity, blocking pipeline delivery."""
    return any(f.severity == Severity.HIGH for f in findings)
