#!/usr/bin/env python3
"""
dispatch_hook.py — CodexAutoAI 共用 UserPromptSubmit dispatch hook。

把所有「輸出可由腳本算出、不需要 LLM」的 slash command 集中到一個註冊表（COMMANDS），
偵測到 bare 指令就用 `decision: block` 即時回覆、**完全不進模型**（零 LLM round-trip，
避免每個固定回覆都花 ~9–11 秒）。非指令的一般 prompt 則維持被動進度注入。

要新增一個即時指令，只要在 COMMANDS 加一筆 `(名稱, handler)`：
    handler(ctx) -> str | None
      回傳字串 → 用該字串 block 回覆；回傳 None → 放行（不 block，例如帶參數時交給 skill）。
    ctx 提供 prompt / args / summary / active 等資訊（見 Context）。

行為（Claude Code UserPromptSubmit 協定，已由 claude-code-guide 確認）：
  - exit 0 + {"decision":"block","reason":"<文字>"} → prompt 不進模型，reason 即時顯示。
  - exit 0 + 純文字 stdout → 當成 context 加進 prompt（被動注入）。
  - UserPromptSubmit 的 matcher 會被忽略，故過濾一律在本腳本內判斷。
  - **fail-open**：任何解析失敗一律不 block，絕不誤擋真正的 prompt。
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# 重用 progress.py 的純函式（read_events / summarize / render），不重寫渲染。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import progress  # noqa: E402

SENTINEL_OPEN = "[codexautoai-progress]"
SENTINEL_CLOSE = "[/codexautoai-progress]"


# ---------------------------------------------------------------------------
# Context：傳給每個 handler 的資訊
# ---------------------------------------------------------------------------
@dataclass
class Context:
    prompt: str          # 完整 prompt 原文
    args: str            # 指令名稱之後的參數（去頭尾空白）
    summary: dict        # progress.summarize() 的結果
    active: bool         # 是否有進行中/已完成的 run


# ---------------------------------------------------------------------------
# 各即時指令的 handler
# ---------------------------------------------------------------------------
_START_GREETING = (
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

_NO_RUN_MSG = (
    "目前沒有進行中的任務（找不到 log/events.jsonl，pipeline 尚未啟動）。\n"
    "\n"
    "要開始的話，打 /start 或直接用一句話描述需求即可，例如"
    "「做一個記帳 CLI 工具，資料存 SQLite」。我會自動跑完七階段"
    "（需求 → 架構 → 審查 → 寫碼 → 測試 → 交付）。"
)


def _handle_start(ctx: Context) -> str | None:
    # 帶了需求（/start <需求>）→ 放行給 dispatcher 開跑；只有 bare /start 才即時回覆。
    if ctx.args:
        return None
    return _START_GREETING


def _handle_progress(ctx: Context) -> str | None:
    # 帶參數（如 /progress --watch）→ 放行給 skill；bare /progress 才即時回覆。
    if ctx.args:
        return None
    return progress.render(ctx.summary, log_exists=True) if ctx.active else _NO_RUN_MSG


# 註冊表：指令名稱（不含斜線）→ handler。新增即時指令在此加一筆即可。
COMMANDS = {
    "start": _handle_start,
    "progress": _handle_progress,
}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent


def _read_prompt() -> str:
    try:
        raw = sys.stdin.read()
        return json.loads(raw).get("prompt", "") if raw.strip() else ""
    except Exception:
        return ""


def _match_command(prompt: str):
    """若 prompt 是已註冊的 slash 指令，回傳 (handler, args)；否則 (None, '')。"""
    m = re.match(r"^\s*/([A-Za-z][\w-]*)(?:\s+(.*))?$", prompt, re.DOTALL)
    if not m:
        return None, ""
    name = m.group(1).lower()
    args = (m.group(2) or "").strip()
    return COMMANDS.get(name), args


def _emit_block(reason: str) -> None:
    json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


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

    handler, args = _match_command(prompt) if isinstance(prompt, str) else (None, "")
    if handler is not None:
        ctx = Context(prompt=prompt, args=args, summary=summary, active=active)
        try:
            reason = handler(ctx)
        except Exception:
            reason = None  # fail-open：handler 出錯就放行，不誤擋
        if reason is not None:
            _emit_block(reason)
            return 0
        # reason is None → 放行（落到下方被動注入）

    # 非即時指令（或指令選擇放行）：有進行中的 run 才被動注入進度 context。
    if active:
        print(SENTINEL_OPEN)
        print(progress.render(summary, log_exists=True))
        print(SENTINEL_CLOSE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
