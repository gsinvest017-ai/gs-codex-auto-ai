#!/usr/bin/env python3
"""
progress_hook.py — CodexAutoAI 進度同窗注入（UserPromptSubmit hook）。

由 Claude Code 的 UserPromptSubmit hook 在每次使用者送出訊息時呼叫，把目前
pipeline 進度以 sentinel 區塊印到 stdout，注入同一個對話視窗——不必另開終端機
跑 `progress.py --watch`。

借鏡 autogo 的 hook 模式：
  - 一律 exit 0（context hook 不可阻斷 prompt）。
  - 閒置時靜默（沒有 log 或沒有任何 phase 事件就不輸出，避免洗版）。

渲染邏輯重用 tools/progress.py 的純函式，不重寫。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 重用同目錄的 progress.py（read_events / summarize / render）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import progress  # noqa: E402

SENTINEL_OPEN = "[codexautoai-progress]"
SENTINEL_CLOSE = "[/codexautoai-progress]"


def _project_dir() -> Path:
    """定位專案根目錄：優先用 hook 環境變數 CLAUDE_PROJECT_DIR，否則回退到
    本檔上層（tools/ 的 parent）。"""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    log_path = _project_dir() / "log" / "events.jsonl"

    # 靜默規則 1：沒有 log → 沒有進行中的 run，不輸出。
    if not log_path.exists():
        return 0

    events = progress.read_events(log_path)
    summary = progress.summarize(events)

    # 靜默規則 2：沒有任何 phase 事件（current 與 completed 都空）→ 不輸出。
    if summary["current"] is None and not summary["completed"]:
        return 0

    body = progress.render(summary, log_exists=True)
    print(SENTINEL_OPEN)
    print(body)
    print(SENTINEL_CLOSE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
