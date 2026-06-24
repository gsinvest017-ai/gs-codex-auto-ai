"""
repo_map.py — Ranked, token-budgeted codebase skeleton for LLMs.

Innovation #9 (Aider repo-map idea): Instead of dumping whole files, give an
LLM only the *signatures* of the most-referenced symbols, greedy-filled to a
character budget.

Stdlib only: ast, pathlib, collections.
No third-party dependencies.  Windows-safe (no POSIX assumptions).
"""
from __future__ import annotations

import ast
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Symbol:
    name: str
    kind: str          # 'function' | 'class'
    file: str
    lineno: int
    signature: str     # e.g. "def foo(a, b)" or "class Bar(Base)"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _args_to_str(args: ast.arguments) -> str:
    """Render an ast.arguments object back to a compact string."""
    parts: list[str] = []

    # positional-only (Python 3.8+)
    for i, arg in enumerate(args.posonlyargs):
        parts.append(arg.arg)
    if args.posonlyargs:
        parts.append("/")

    # regular args (with defaults filled from the right)
    n_defaults = len(args.defaults)
    n_args = len(args.args)
    for i, arg in enumerate(args.args):
        default_index = i - (n_args - n_defaults)
        if default_index >= 0:
            parts.append(f"{arg.arg}=...")
        else:
            parts.append(arg.arg)

    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")

    # keyword-only args
    for i, arg in enumerate(args.kwonlyargs):
        if args.kw_defaults[i] is not None:
            parts.append(f"{arg.arg}=...")
        else:
            parts.append(arg.arg)

    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")

    return ", ".join(parts)


def _bases_to_str(bases: list[ast.expr]) -> str:
    """Render base-class list to a string (best-effort, no eval)."""
    result = []
    for base in bases:
        if isinstance(base, ast.Name):
            result.append(base.id)
        elif isinstance(base, ast.Attribute):
            parts = []
            node: ast.expr = base
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            result.append(".".join(reversed(parts)))
        else:
            result.append("...")
    return ", ".join(result)


# ---------------------------------------------------------------------------
# extract_symbols
# ---------------------------------------------------------------------------

def extract_symbols(py_source: str, filename: str = "<mem>") -> list[Symbol]:
    """
    Parse *py_source* with ast and return top-level + class-method symbols.

    Includes:
    - Top-level functions (ast.FunctionDef / ast.AsyncFunctionDef)
    - Top-level classes (ast.ClassDef)
    - Methods inside top-level classes (one level deep)

    Returns an empty list on syntax errors.
    """
    try:
        tree = ast.parse(py_source, filename=filename)
    except SyntaxError:
        return []

    symbols: list[Symbol] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            sig = f"{prefix} {node.name}({_args_to_str(node.args)})"
            symbols.append(Symbol(
                name=node.name,
                kind="function",
                file=filename,
                lineno=node.lineno,
                signature=sig,
            ))

        elif isinstance(node, ast.ClassDef):
            bases_str = _bases_to_str(node.bases)
            if bases_str:
                sig = f"class {node.name}({bases_str})"
            else:
                sig = f"class {node.name}"
            symbols.append(Symbol(
                name=node.name,
                kind="class",
                file=filename,
                lineno=node.lineno,
                signature=sig,
            ))
            # methods one level deep
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async def" if isinstance(child, ast.AsyncFunctionDef) else "def"
                    msig = f"{prefix} {child.name}({_args_to_str(child.args)})"
                    symbols.append(Symbol(
                        name=child.name,
                        kind="function",
                        file=filename,
                        lineno=child.lineno,
                        signature=msig,
                    ))

    return symbols


# ---------------------------------------------------------------------------
# rank_symbols
# ---------------------------------------------------------------------------

def _collect_references(py_source: str) -> Counter[str]:
    """Count every ast.Name and ast.Attribute identifier used in a source."""
    try:
        tree = ast.parse(py_source)
    except SyntaxError:
        return Counter()

    counter: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            counter[node.id] += 1
        elif isinstance(node, ast.Attribute):
            counter[node.attr] += 1
    return counter


def rank_symbols(files: dict[str, str]) -> list[tuple[Symbol, int]]:
    """
    Extract all symbols from *files* and rank them by reference count.

    Reference count = number of times the symbol's *name* appears as
    ast.Name or ast.Attribute across ALL files combined (PageRank-lite).

    Returns list of (Symbol, ref_count) sorted by ref_count descending,
    then by (file, lineno) for stable ordering of ties.
    """
    if not files:
        return []

    # Collect all symbols
    all_symbols: list[Symbol] = []
    for filename, source in files.items():
        all_symbols.extend(extract_symbols(source, filename=filename))

    if not all_symbols:
        return []

    # Count references across all files
    global_refs: Counter[str] = Counter()
    for source in files.values():
        global_refs.update(_collect_references(source))

    # Build (symbol, count) pairs — subtract 1 for the definition site itself
    # (the name appears at the def/class statement too, so we reduce noise by
    # not penalising, but we do NOT double-subtract if defined multiple times)
    ranked: list[tuple[Symbol, int]] = []
    for sym in all_symbols:
        count = global_refs.get(sym.name, 0)
        ranked.append((sym, count))

    ranked.sort(key=lambda t: (-t[1], t[0].file, t[0].lineno))
    return ranked


# ---------------------------------------------------------------------------
# build_map
# ---------------------------------------------------------------------------

def build_map(files: dict[str, str], max_chars: int = 2000) -> str:
    """
    Build a compact, token-budgeted repo-map string.

    Layout::

        ── filename.py ──
        def foo(a, b)
        class Bar(Base)
          def method(self)

    Symbols are inserted in decreasing rank order, grouped by file.
    The method is greedy: we add signatures until the budget is exhausted.
    The output length is <= max_chars (each line is measured before appending).
    """
    if not files:
        return ""

    ranked = rank_symbols(files)
    if not ranked:
        return ""

    # Build an ordered mapping: file -> [(symbol, rank)]
    # We iterate ranked (high to low) so within a file the order still
    # reflects importance.
    file_order: list[str] = []
    file_map: dict[str, list[tuple[Symbol, int]]] = {}
    for sym, cnt in ranked:
        if sym.file not in file_map:
            file_map[sym.file] = []
            file_order.append(sym.file)
        file_map[sym.file].append((sym, cnt))

    lines: list[str] = []
    chars_used = 0

    def try_add(line: str) -> bool:
        nonlocal chars_used
        needed = len(line) + 1  # +1 for newline
        if chars_used + needed > max_chars:
            return False
        lines.append(line)
        chars_used += needed
        return True

    for fname in file_order:
        header = f"── {fname} ──"
        if not try_add(header):
            break
        for sym, _ in file_map[fname]:
            # Indent methods (they have 'self' / are named like methods but
            # we detect them by checking if a class with same file precedes)
            indent = "  " if sym.kind == "function" and _is_method(sym, file_map[fname]) else ""
            if not try_add(f"{indent}{sym.signature}"):
                # Budget exhausted mid-file — stop entirely
                return "\n".join(lines)

    return "\n".join(lines)


def _is_method(sym: Symbol, file_symbols: list[tuple[Symbol, int]]) -> bool:
    """
    Heuristic: a function symbol is a 'method' if a class symbol from the
    same file appears before it (by lineno) and no other function at the
    top level sits between them.

    We re-use the already-extracted symbol list for the file.
    """
    # Find last class that started before this symbol
    for other_sym, _ in file_symbols:
        if (
            other_sym.kind == "class"
            and other_sym.file == sym.file
            and other_sym.lineno < sym.lineno
        ):
            return True
    return False
