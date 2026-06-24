"""
safety.py — CodexAutoAI v2 Safety & Security-Governance primitives.

Implements:
  SAFE-R2  : deny-list for irreversible git ops (evaluate)
  SAFE-R4  : dynamic toolchain path resolution (resolve_tool)
  SECGOV-R5: framework integrity boundary (is_framework_path, assert_writable)
  SECGOV-R6: MODE3 out-of-band authorization only (mode3_authorized)
  SECGOV-R7: prohibited git ops enforced at permission layer (PermissionPolicy)

Pure stdlib: shutil, os, re, pathlib — no external dependencies.
Windows-safe: no hardcoded absolute paths.
"""

from __future__ import annotations

import re
import shutil
from pathlib import PurePosixPath, PureWindowsPath

# ---------------------------------------------------------------------------
# SAFE-R2 / SECGOV-R7 — Irreversible-operation deny patterns
# ---------------------------------------------------------------------------

PROHIBITED_PATTERNS: list[str] = [
    # git push (any form, including --force / -f)
    r"\bgit\s+push\b",
    # git commit (including --amend)
    r"\bgit\s+commit\b",
    # git reset --hard
    r"\bgit\s+reset\b.*--hard",
    # git rebase
    r"\bgit\s+rebase\b",
    # git filter-branch
    r"\bgit\s+filter-branch\b",
    # force-push shorthands: --force or -f with push already caught above,
    # but also catch explicit force-push syntax
    r"\bgit\s+.*--force\b",
    r"\bgit\s+.*-f\b",
]

# ---------------------------------------------------------------------------
# SECGOV-R5 — Framework paths that agents must NOT write to
# ---------------------------------------------------------------------------

_FRAMEWORK_PATH_SEGMENTS: tuple[str, ...] = (
    ".claude",
    "CLAUDE.md",
    "DESIGN",
    "project.md",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FrameworkIntegrityError(Exception):
    """Raised when a write to a protected framework path is attempted."""


# ---------------------------------------------------------------------------
# PermissionPolicy — SAFE-R2 / SECGOV-R7
# ---------------------------------------------------------------------------


class PermissionPolicy:
    """Evaluate shell commands against allow/deny rules.

    Parameters
    ----------
    allow:
        Glob-like patterns of allowed commands.  ``('*',)`` means allow all
        commands that are not explicitly denied.
    deny:
        Extra deny patterns (in addition to PROHIBITED_PATTERNS).  Each entry
        is a regex string.
    """

    def __init__(
        self,
        allow: tuple[str, ...] = ("*",),
        deny: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self._allow = allow
        extra_deny: list[str] = list(deny) if deny else []
        self._deny_patterns: list[str] = PROHIBITED_PATTERNS + extra_deny

    # ------------------------------------------------------------------
    def evaluate(self, command: str) -> str:
        """Return ``'deny'`` if *command* matches a prohibited pattern,
        otherwise ``'allow'``.

        The check is case-insensitive and matches anywhere in the command
        string so that multi-word compound commands are caught.
        """
        for pattern in self._deny_patterns:
            if re.search(pattern, command, flags=re.IGNORECASE):
                return "deny"
        return "allow"


# ---------------------------------------------------------------------------
# SECGOV-R5 helpers
# ---------------------------------------------------------------------------


def _normalise_path_parts(path: str) -> list[str]:
    """Return the individual parts of *path* normalised for comparison.

    Works on both POSIX (``/``) and Windows (``\\``) path separators so the
    function is portable across operating systems without relying on the host
    OS path flavour.
    """
    # Normalise separators to forward-slash then split
    normalised = path.replace("\\", "/").strip("/")
    return [p for p in normalised.split("/") if p]


def is_framework_path(path: str) -> bool:
    """Return ``True`` if *path* contains any protected framework segment.

    A path is considered a framework path if any component of the path
    *equals* a protected segment (case-sensitive, matching how the framework
    names its files on all supported OS).

    Examples
    --------
    >>> is_framework_path('.claude/agents/x.md')
    True
    >>> is_framework_path('CLAUDE.md')
    True
    >>> is_framework_path('src/foo.py')
    False
    """
    parts = _normalise_path_parts(path)
    for segment in _FRAMEWORK_PATH_SEGMENTS:
        if segment in parts:
            return True
        # Also catch the file name if the path *is* the file (no directory
        # component), e.g. ``assert_writable('CLAUDE.md')``.
        if parts and parts[-1] == segment:
            return True
    return False


def assert_writable(path: str) -> None:
    """Raise :exc:`FrameworkIntegrityError` if *path* is a framework path.

    Parameters
    ----------
    path:
        The file or directory path to check (relative or absolute).

    Raises
    ------
    FrameworkIntegrityError
        When *path* resolves to a protected framework location.
    """
    if is_framework_path(path):
        raise FrameworkIntegrityError(
            f"Write to framework path denied: {path!r}. "
            "Agents may not modify '.claude/', 'CLAUDE.md', 'DESIGN/', or 'project.md'."
        )


# ---------------------------------------------------------------------------
# SAFE-R4 — Dynamic toolchain resolution
# ---------------------------------------------------------------------------


def resolve_tool(name: str) -> str | None:
    """Resolve *name* to an absolute executable path using :func:`shutil.which`.

    Returns ``None`` if the tool is not found on ``PATH``.  Never hard-codes
    user-specific or machine-specific paths (SAFE-R4).

    Parameters
    ----------
    name:
        The bare executable name, e.g. ``'python'``, ``'uv'``, ``'git'``.
    """
    return shutil.which(name)


# ---------------------------------------------------------------------------
# SECGOV-R6 / C11 — MODE3 out-of-band authorization
# ---------------------------------------------------------------------------


def mode3_authorized(token: str | None, *, embedded: bool = False) -> bool:
    """Check whether a MODE3 (implementation) authorization is valid.

    Authorization rules (SECGOV-R6, constitution C11):

    1. If *embedded* is ``True`` — the token arrived as content embedded
       inside a requirement or data payload — it is **never** authorizing,
       regardless of the token value.  This prevents prompt-injection
       self-authorization.
    2. If *embedded* is ``False`` (out-of-band delivery) and *token* is a
       non-empty string, the authorization is valid.
    3. A ``None`` or empty token is always unauthorized.

    Parameters
    ----------
    token:
        The authorization token supplied by the caller.
    embedded:
        ``True`` when the token was sourced from within requirement/data
        content (untrusted channel).  ``False`` (default) for an explicit
        human-supplied out-of-band token.

    Returns
    -------
    bool
        ``True`` only for a valid out-of-band non-empty token.
    """
    if embedded:
        # Injection path — never authorizes, even if the token looks valid.
        return False
    if not token:
        return False
    return True
