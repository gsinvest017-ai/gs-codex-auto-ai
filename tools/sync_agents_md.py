#!/usr/bin/env python3
"""
sync_agents_md.py — 由 CLAUDE.md 單向生成 AGENTS.md。

背景：OpenAI Codex CLI 等工具會讀取 `AGENTS.md` 作為專案指令；而本專案的
指令 SSOT 是 `CLAUDE.md`（project.md C3：單一事實來源，禁止雙寫分岔）。
本腳本把 `AGENTS.md` 變成 `CLAUDE.md` 的**唯一向產物**——只能改 CLAUDE.md，
AGENTS.md 由此重新生成，永不手動編輯。

用法：
    python tools/sync_agents_md.py           # 重新生成 AGENTS.md
    python tools/sync_agents_md.py --check    # 只檢查是否同步；不同步則 exit 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "CLAUDE.md"
TARGET = ROOT / "AGENTS.md"

BANNER = (
    "<!-- 此檔由 CLAUDE.md 自動生成，請勿手動編輯。"
    "改指令請改 CLAUDE.md，再執行 `python tools/sync_agents_md.py`。 -->\n\n"
)


def expected_content() -> str:
    """AGENTS.md 應有的內容 = 橫幅 + CLAUDE.md 全文。"""
    if not SOURCE.exists():
        raise FileNotFoundError(f"找不到來源檔：{SOURCE}")
    return BANNER + SOURCE.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="由 CLAUDE.md 生成 AGENTS.md")
    ap.add_argument("--check", action="store_true",
                    help="只檢查是否同步，不寫入；不同步則 exit 1")
    args = ap.parse_args(argv)

    # Windows 主控台預設 cp950 無法輸出部分符號，強制 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    want = expected_content()

    if args.check:
        have = TARGET.read_text(encoding="utf-8") if TARGET.exists() else None
        if have == want:
            print("AGENTS.md 與 CLAUDE.md 同步 ✓")
            return 0
        print("AGENTS.md 與 CLAUDE.md 不同步 ✗ — 請執行 "
              "`python tools/sync_agents_md.py`", file=sys.stderr)
        return 1

    TARGET.write_text(want, encoding="utf-8")
    print(f"已由 CLAUDE.md 生成 {TARGET.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
