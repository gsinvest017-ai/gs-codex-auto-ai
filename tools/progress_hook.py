#!/usr/bin/env python3
"""
progress_hook.py — 進度相關的 UserPromptSubmit hook，兩種職責：

1. **bare `/progress` 即時回覆（零 LLM round-trip）**
   使用者只打 `/progress` 時，進度是可由腳本算出的固定資訊，不需要跑 Opus 推論
   （~11 秒）。本 hook 偵測到就用 `decision: block` 直接把進度回給使用者、完全不進
   模型，延遲降到幾十毫秒。

2. **被動進度注入（其他 prompt）**
   非 `/progress` 的一般 prompt：若有進行中的 run，就把進度以 sentinel 區塊
   注入同一個對話視窗（context），讓使用者每次送訊息都看得到目前 phase。

行為（Claude Code UserPromptSubmit 協定）：
  - exit 0 + {"decision":"block","reason":"<文字>"} → prompt 不送進模型，reason 即時
    顯示給使用者（用於 bare /progress）。
  - exit 0 + 純文字 stdout → 當成 context 加進 prompt（用於被動注入）。

規則：
  - **fail-open**：stdin 壞掉一律不 block（最多退化成被動注入或靜默），絕不誤擋 prompt。
  - 閒置時靜默（沒有 log 或沒有任何 phase 事件，且非 /progress）→ 不輸出。
  - UserPromptSubmit 的 matcher 會被 Claude Code 忽略，故過濾邏輯一律在本腳本內判斷。

渲染邏輯重用 tools/progress.py 的純函式，不重寫。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# 重用同目錄的 progress.py（read_events / summarize / render）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import progress  # noqa: E402

SENTINEL_OPEN = "[codexautoai-progress]"
SENTINEL_CLOSE = "[/codexautoai-progress]"

_BARE_PROGRESS = re.compile(r"^\s*/progress\s*$")

NO_RUN_MSG = (
    "目前沒有進行中的任務（找不到 log/events.jsonl，pipeline 尚未啟動）。\n"
    "\n"
    "要開始的話，打 /start 或直接用一句話描述需求即可，例如"
    "「做一個記帳 CLI 工具，資料存 SQLite」。我會自動跑完七階段"
    "（需求 → 架構 → 審查 → 寫碼 → 測試 → 交付）。"
)


def _project_dir() -> Path:
    """定位專案根目錄：優先用 hook 環境變數 CLAUDE_PROJECT_DIR，否則回退到
    本檔上層（tools/ 的 parent）。"""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def _read_prompt() -> str:
    """fail-open 讀取 stdin 的 prompt；任何失敗回空字串。"""
    try:
        raw = sys.stdin.read()
        return json.loads(raw).get("prompt", "") if raw.strip() else ""
    except Exception:
        return ""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    prompt = _read_prompt()
    log_path = _project_dir() / "log" / "events.jsonl"

    events = progress.read_events(log_path) if log_path.exists() else []
    summary = progress.summarize(events)
    active = summary["current"] is not None or bool(summary["completed"])

    # 職責 1：bare /progress → 即時 block 回覆（零 LLM）
    if isinstance(prompt, str) and _BARE_PROGRESS.match(prompt):
        reason = progress.render(summary, log_exists=True) if active else NO_RUN_MSG
        json.dump({"decision": "block", "reason": reason},
                  sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    # 職責 2：其他 prompt → 有進行中的 run 才被動注入進度 context；否則靜默
    if active:
        print(SENTINEL_OPEN)
        print(progress.render(summary, log_exists=True))
        print(SENTINEL_CLOSE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
