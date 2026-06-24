#!/usr/bin/env python3
"""
repo_context.py — Stage 4：給 Codex prompt 一份預算內的 repo-map（省 token）。

重用 v2 的 `repo_map.build_map`（Aider 技術）：與其把整檔塞進 Codex prompt，不如給
依賴檔的 ranked、char-budget 的 symbol 摘要。減少 Codex input token 與介面誤配重工。

用法（在 builder / fix prompt 內嵌）：
    codex exec --full-auto "...上下文：$(python tools/repo_context.py --files src/a.py src/b.py)..."

    python tools/repo_context.py --files <f1> <f2> ... [--max-chars 2000]
印出 repo-map 文字到 stdout（找不到檔或出錯則印空字串，fail-open）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent.parent


def build(files: list[str], max_chars: int) -> str:
    if str(_TOOL_ROOT) not in sys.path:
        sys.path.insert(0, str(_TOOL_ROOT))
    from src.codexautoai_v2.repo_map import build_map  # noqa: E402
    contents: dict[str, str] = {}
    for f in files:
        try:
            contents[f] = Path(f).read_text(encoding="utf-8")
        except Exception:
            continue
    if not contents:
        return ""
    return build_map(contents, max_chars=max_chars)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="輸出依賴檔的 repo-map（省 Codex token）")
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--max-chars", type=int, default=2000)
    args = ap.parse_args(argv)
    try:
        print(build(args.files, args.max_chars))
    except Exception as exc:  # noqa: BLE001 — fail-open
        print("", end="")
        print(f"repo_context: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
