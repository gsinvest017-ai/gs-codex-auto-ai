#!/usr/bin/env python3
"""
install_hooks.py — 啟用本 repo 的 git hooks（一次性設定）。

git hooks 預設不隨 clone 啟用，需把 git 的 hooksPath 指到版控內的 .githooks。
clone 後跑一次即可：

    python tools/install_hooks.py

之後 commit 時 .githooks/pre-commit 會自動由 CLAUDE.md 重生 AGENTS.md。
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = ROOT / ".githooks"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    if not HOOKS_DIR.is_dir():
        print(f"找不到 {HOOKS_DIR}，無法安裝。", file=sys.stderr)
        return 1

    # 設定 core.hooksPath（相對路徑，跨平台、可移植）
    subprocess.run(
        ["git", "-C", str(ROOT), "config", "core.hooksPath", ".githooks"],
        check=True,
    )

    # 確保 hook 在 POSIX 系統可執行（Windows 上 git 不看此位元，但無害）
    for hook in HOOKS_DIR.iterdir():
        if hook.is_file():
            mode = hook.stat().st_mode
            hook.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print("✓ 已啟用 git hooks（core.hooksPath = .githooks）。")
    print("  之後 commit 時會自動由 CLAUDE.md 重生 AGENTS.md。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
