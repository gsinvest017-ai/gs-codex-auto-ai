#!/usr/bin/env python3
"""
progress.py — CodexAutoAI 最小進度視圖（終端機）。

從 log/events.jsonl（OBS-R2 結構化事件）還原目前跑到哪一個 phase、
當前迭代、累計成本，並印出一條進度條。純標準庫、不硬編路徑（C9）。

事件解析與渲染邏輯已抽到 `tools/events_model.py`（共用讀取層），本檔只保留
CLI 與向後相容的匯出（`read_events` / `summarize` / `render` 供 `dispatch_hook.py`
沿用）。desktop 面板與 VS Code extension 走同一個 events_model，三邊不會漂移。

用法：
    python tools/progress.py            # 印一次目前狀態
    python tools/progress.py --watch    # 每 2 秒刷新一次
    python tools/progress.py --log path/to/events.jsonl  # 指定日誌位置
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from events_model import (  # noqa: E402
    PHASES,
    TOTAL,
    read_events,
    render_summary_lines,
    summarize,
)

__all__ = ["PHASES", "TOTAL", "read_events", "summarize", "render", "main"]


def render(summary: dict, log_exists: bool) -> str:
    """渲染成單一字串（既有契約：`dispatch_hook.py` 直接用回傳值印出）。"""
    return "\n".join(render_summary_lines(summary, log_exists=log_exists))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CodexAutoAI 進度視圖")
    ap.add_argument("--log", default="log/events.jsonl",
                    help="事件日誌路徑（預設 log/events.jsonl）")
    ap.add_argument("--watch", action="store_true", help="持續刷新（每 2 秒）")
    args = ap.parse_args(argv)

    # Windows 主控台預設 cp950 無法輸出進度條字元與部分中文，強制 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    log_path = Path(args.log)

    def show() -> None:
        events = read_events(log_path)
        print(render(summarize(events), log_exists=log_path.exists()))

    if not args.watch:
        show()
        return 0

    try:
        while True:
            # 清螢幕（跨平台：ANSI；Windows 10+ 終端機支援）
            sys.stdout.write("\033[2J\033[H")
            show()
            sys.stdout.flush()
            time.sleep(2)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
