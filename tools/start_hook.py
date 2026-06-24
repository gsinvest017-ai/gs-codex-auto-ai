#!/usr/bin/env python3
"""
start_hook.py — bare `/start` 即時回覆（UserPromptSubmit hook，零 LLM round-trip）。

當使用者只打 `/start`（沒帶需求）時，輸出固定的歡迎/反問文字本來要跑一次 Opus
推論（~9 秒）。本 hook 偵測到 bare `/start` 就用 `decision: block` 直接把文字回給
使用者、完全不進 LLM，延遲降到幾十毫秒。

行為（Claude Code UserPromptSubmit 協定）：
  - exit 0 + {"decision":"block","reason":"<文字>"} → prompt 不送進模型，reason 即時
    顯示給使用者。
  - exit 0 + 無輸出 → prompt 照常進入 dispatcher。

規則：
  - 只攔 bare `/start`（`/start` 後面沒有任何非空白內容）。
  - `/start <需求>`、其他任何輸入 → 靜默放行（無輸出）。
  - **fail-open**：stdin 壞掉、非預期狀況一律放行，絕不誤擋真正的 prompt。
  - UserPromptSubmit 的 matcher 會被 Claude Code 忽略，故過濾邏輯一律在本腳本內判斷。

歡迎文字與 `.claude/skills/start/SKILL.md` 情況 B 保持一致（該檔為人類可讀的權威來源；
本處為加速用的同步副本）。
"""
from __future__ import annotations

import json
import re
import sys

# bare /start：開頭可有空白，/start 之後到結尾只能是空白
_BARE_START = re.compile(r"^\s*/start\s*$")

GREETING = (
    "我是 CodexAutoAI 調度中心。你只要告訴我想做什麼，我會自動跑完\n"
    "需求分析 → 架構 → 審查 → 寫碼 → 測試 → 交付，中途只在需求需要\n"
    "澄清時停下來問你。\n"
    "\n"
    "請描述你的需求，例如：\n"
    "\n"
    "- 「做一個記帳 CLI 工具，資料存 SQLite」\n"
    "- 「寫一個爬蟲，抓某網站的文章標題存成 CSV」\n"
    "- 「做一個 FastAPI 後端，提供待辦事項的 CRUD API」\n"
    "\n"
    "你想做什麼？"
)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    # fail-open：任何讀取/解析失敗都放行（無輸出、exit 0）。
    try:
        raw = sys.stdin.read()
        prompt = json.loads(raw).get("prompt", "") if raw.strip() else ""
    except Exception:
        return 0

    if isinstance(prompt, str) and _BARE_START.match(prompt):
        json.dump({"decision": "block", "reason": GREETING},
                  sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
