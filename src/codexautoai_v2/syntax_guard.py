"""
syntax_guard.py — CodexAutoAI v2 Syntax Guard (BUILD-R5).

Implements the SWE-agent ACI lint-guard: REJECT any edit that makes a source
file fail to parse, returning the PRECISE syntax error (line/col + message)
and nearby lines.  This guard is run BEFORE accepting a builder's write.

Supported file types
--------------------
- ``.py``  — validated via :mod:`ast` (``ast.parse``)
- ``.json`` — validated via :mod:`json` (``json.loads``)
- other    — accepted with a note (no parser available)

Pure stdlib: ast, json, io — no external dependencies.
Windows-safe: no OS-specific paths or calls.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class GuardResult:
    """Outcome of a syntax check.

    Attributes
    ----------
    ok:
        ``True`` when the source parsed successfully (or the extension is
        unknown and we have no parser to apply).
    error:
        Human-readable error message on failure; ``None`` on success.
    line:
        1-based line number where the syntax error occurred; ``None`` if
        not applicable or not available.
    col:
        1-based column offset where the syntax error occurred; ``None`` if
        not applicable or not available.
    context:
        A short excerpt of the source centred on the error line (up to two
        surrounding lines); ``None`` when there is no error or the source
        has no lines to show.
    """

    ok: bool
    error: Optional[str] = None
    line: Optional[int] = None
    col: Optional[int] = None
    context: Optional[str] = None


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class SyntaxGuardError(Exception):
    """Raised by :func:`guard_write` when a file fails the syntax check.

    The offending :class:`GuardResult` is stored on the ``result`` attribute.
    """

    def __init__(self, result: GuardResult) -> None:
        self.result = result
        super().__init__(
            f"Syntax guard rejected write: {result.error} "
            f"(line {result.line}, col {result.col})"
        )


# ---------------------------------------------------------------------------
# _context_lines — extract nearby source lines around an error
# ---------------------------------------------------------------------------


def _context_lines(source: str, error_line: int, radius: int = 2) -> str:
    """Return *radius* lines before and after *error_line* from *source*.

    Parameters
    ----------
    source:
        The full source text.
    error_line:
        1-based line number of the error.
    radius:
        Number of lines to include on each side of the error line.

    Returns
    -------
    str
        A multi-line string with each line prefixed by its line number and a
        ``>`` marker on the error line, e.g.::

            1:  def f():
            2>      syntax error here
            3:      pass
    """
    lines = source.splitlines()
    start = max(0, error_line - 1 - radius)
    end = min(len(lines), error_line + radius)
    parts: list[str] = []
    for idx in range(start, end):
        lineno = idx + 1  # 1-based
        marker = ">" if lineno == error_line else " "
        parts.append(f"{lineno}{marker} {lines[idx]}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# check_python
# ---------------------------------------------------------------------------


def check_python(source: str) -> GuardResult:
    """Validate *source* as Python using :func:`ast.parse`.

    Parameters
    ----------
    source:
        Python source code as a string.

    Returns
    -------
    GuardResult
        ``ok=True`` when the source is valid.  On :exc:`SyntaxError` the
        result carries ``ok=False``, the precise ``line``/``col``, the error
        ``message``, and a ``context`` snippet.
    """
    try:
        ast.parse(source)
        return GuardResult(ok=True)
    except SyntaxError as exc:
        line: Optional[int] = exc.lineno
        # PEP 617 / CPython 3.10+ may provide end_lineno; we use lineno only.
        # exc.offset is 1-based column (may be None for some internal errors).
        col: Optional[int] = exc.offset
        msg: str = exc.msg or str(exc)
        ctx: Optional[str] = _context_lines(source, line) if line is not None else None
        return GuardResult(ok=False, error=msg, line=line, col=col, context=ctx)


# ---------------------------------------------------------------------------
# check_json
# ---------------------------------------------------------------------------


def check_json(source: str) -> GuardResult:
    """Validate *source* as JSON using :func:`json.loads`.

    Parameters
    ----------
    source:
        JSON source text.

    Returns
    -------
    GuardResult
        ``ok=True`` on valid JSON.  On :exc:`json.JSONDecodeError` the result
        carries the error message, ``line``, and ``col`` extracted from the
        decoder exception, plus a context snippet.
    """
    try:
        json.loads(source)
        return GuardResult(ok=True)
    except json.JSONDecodeError as exc:
        line: int = exc.lineno   # 1-based
        col: int = exc.colno     # 1-based column
        msg: str = exc.msg
        ctx: str = _context_lines(source, line)
        return GuardResult(ok=False, error=msg, line=line, col=col, context=ctx)


# ---------------------------------------------------------------------------
# check — dispatch by file extension
# ---------------------------------------------------------------------------

_NOTE_UNKNOWN = "no parser available for this file type; accepted without validation"


def check(filename: str, source: str) -> GuardResult:
    """Dispatch a syntax check based on the file extension of *filename*.

    Supported extensions
    --------------------
    ``.py``
        Delegates to :func:`check_python`.
    ``.json``
        Delegates to :func:`check_json`.
    *other*
        Returns ``ok=True`` with a note in ``error`` (not a failure — the
        field is reused to carry the informational note when ``ok`` is
        ``True`` and no parser is available).

    Parameters
    ----------
    filename:
        The target file name (basename or full path).  Only the extension
        is inspected.
    source:
        The file's proposed content.

    Returns
    -------
    GuardResult
    """
    # Normalise: handle both forward- and back-slashes, then extract extension.
    normalised = filename.replace("\\", "/")
    if "." in normalised.split("/")[-1]:
        ext = "." + normalised.rsplit(".", 1)[-1].lower()
    else:
        ext = ""

    if ext == ".py":
        return check_python(source)
    if ext == ".json":
        return check_json(source)
    # Unknown extension — accept with informational note.
    return GuardResult(ok=True, error=_NOTE_UNKNOWN)


# ---------------------------------------------------------------------------
# guard_write — raise on failure
# ---------------------------------------------------------------------------


def guard_write(filename: str, source: str) -> None:
    """Run the syntax check for *filename* / *source* and raise on failure.

    This is the main entry point for builders.  Call it **before** writing
    *source* to disk.  If the check fails, a :exc:`SyntaxGuardError` is raised
    carrying the full :class:`GuardResult` so the caller can report the precise
    error location.

    Parameters
    ----------
    filename:
        The destination file name (used to determine the parser).
    source:
        The proposed file content.

    Raises
    ------
    SyntaxGuardError
        When the source fails the syntax check for its file type.
    """
    result = check(filename, source)
    if not result.ok:
        raise SyntaxGuardError(result)
