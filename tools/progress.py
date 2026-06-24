#!/usr/bin/env python3
"""
progress.py — CodexAutoAI 最小進度視圖。

從 log/events.jsonl（OBS-R2 結構化事件）還原目前跑到哪一個 phase、
當前迭代、累計成本，並印出一條進度條。純標準庫、不硬編路徑（C9）。

用法：
    python tools/progress.py            # 印一次目前狀態
    python tools/progress.py --watch    # 每 2 秒刷新一次
    python tools/progress.py --log path/to/events.jsonl  # 指定日誌位置
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Phase 編號 → 中文名稱（對應 CLAUDE.md 的七階段）
PHASES: dict[int, str] = {
    0: "初始化",
    1: "環境檢查",
    2: "需求分析",
    3: "架構設計",
    4: "審查",
    5: "並行開發",
    6: "測試",
    7: "交付",
}
TOTAL = max(PHASES)  # 7


def _phase_num(value) -> int | None:
    """把事件裡的 phase 欄位（如 'phase3' / 3 / '3'）轉成整數。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).lower().removeprefix("phase").strip()
    return int(s) if s.isdigit() else None


def read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    events: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 容忍半寫入的最後一行
    return events


def summarize(events: list[dict]) -> dict:
    """回傳目前狀態摘要。"""
    current = None          # 最近一個 phase_start 的 phase 編號
    completed: set[int] = set()
    iteration = 0
    cost = 0.0
    last_status = None
    failed = False

    for ev in events:
        etype = ev.get("event_type")
        pnum = _phase_num(ev.get("phase"))
        if etype == "phase_start" and pnum is not None:
            current = pnum
        elif etype == "phase_end" and pnum is not None:
            if ev.get("status") == "success":
                completed.add(pnum)
            elif ev.get("status") == "failure":
                failed = True
        if ev.get("iteration") is not None:
            iteration = ev["iteration"]
        if ev.get("cumulative_cost_usd") is not None:
            cost = ev["cumulative_cost_usd"]
        if ev.get("status"):
            last_status = ev["status"]
        if etype == "error":
            failed = True

    return {
        "current": current,
        "completed": completed,
        "iteration": iteration,
        "cost": cost,
        "last_status": last_status,
        "failed": failed,
    }


def render(summary: dict, log_exists: bool) -> str:
    if not log_exists:
        return ("[CodexAutoAI] 尚未開始（找不到 log/events.jsonl）。\n"
                "在 Claude Code 裡打 /start 或描述需求即可啟動。")

    cur = summary["current"]
    done = summary["completed"]
    # 進度條取「當前 phase」與「已完成的最高 phase」較大者，
    # 即使某個 phase_start 漏接，進度條也不會卡住。
    marker = max((cur or 0), (max(done) if done else 0))

    bar = "".join("▓" if i <= marker else "░" for i in range(TOTAL + 1))
    name = PHASES.get(marker, "?")
    state = "✗ 失敗/升級" if summary["failed"] else "● 進行中"
    if marker == TOTAL and TOTAL in done:
        state = "✓ 完成"

    lines = [
        f"[CodexAutoAI] Phase {marker}/{TOTAL} {bar} {name}  {state}",
        f"            已完成階段：{sorted(done) if done else '無'}",
    ]
    if summary["iteration"]:
        lines.append(f"            當前迭代：第 {summary['iteration']} 輪（守衛上限 3）")
    if summary["cost"]:
        lines.append(f"            累計成本：${summary['cost']:.4f} USD")
    return "\n".join(lines)


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
        print(render(summarize(events), log_path.exists()))

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
