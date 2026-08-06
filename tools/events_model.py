#!/usr/bin/env python3
"""
events_model.py — `log/events.jsonl` 的單一事實模型（SSOT），供所有 UI 共用。

在這之前，`log/events.jsonl` 只有 `progress.py` 一個消費者、只渲染成終端機文字，
所以 desktop App 與 VS Code extension 都看不到 pipeline 跑到哪（只能開終端機看）。
本模組把「事件 → 結構化狀態」這層抽出來當共用地基：

    progress.py          → 終端機進度條（既有行為不變，改為委派本模組）
    desktop/launcher.py  → tkinter 進度面板
    vscode-extension     → TreeView / StatusBar（透過 `--json` 走 child_process）

設計約束：
  * **純標準庫**。本框架會被打包進公開發行的 App / vsix，且會被丟進使用者的專案裡
    直接跑，不能引入任何第三方或私有依賴（同 `desktop/launcher.py` 的既有不變式）。
  * **唯讀**。只讀 events.jsonl，永不寫入、永不改 pipeline 狀態。
  * **容忍半寫入**：pipeline 正在 append 時最後一行可能不完整，壞行一律跳過。

事件 schema 來自 `src/codexautoai_v2/events.py` 的 `EventBus.emit`：
每行至少有 `event_type` 與 `timestamp`，phase 事件帶 `phase`，
迴圈 tick 帶 `iteration` / `cumulative_cost_usd`，錯誤帶 `reason` / `status`。

用法：
    python tools/events_model.py                 # 印人類可讀摘要
    python tools/events_model.py --json          # 印 JSON（給 extension / 面板消費）
    python tools/events_model.py --log path.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Phase 編號 → 中文名稱（對應 CLAUDE.md 的七階段）。
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

# pipeline 整體狀態（給 UI 決定圖示 / 顏色）。
STATE_NOT_STARTED = "not_started"
STATE_RUNNING = "running"
STATE_ESCALATED = "escalated"
STATE_DONE = "done"

# 沒掛在任何 phase 底下的錯誤（run 層級），沒有「該 phase 後來成功了」可以把它清掉。
_RUN_LEVEL_FAILURE = -1


def phase_num(value: object) -> int | None:
    """把事件裡的 phase 欄位（`'phase3'` / `3` / `'3'`）正規化成整數。"""
    if value is None:
        return None
    if isinstance(value, bool):        # bool 是 int 的子型別，先擋掉
        return None
    if isinstance(value, int):
        return value
    s = str(value).lower().removeprefix("phase").strip()
    return int(s) if s.isdigit() else None


def read_events(log_path: str | Path) -> list[dict]:
    """讀 events.jsonl 成 dict list；檔案不存在回空 list，壞行跳過。"""
    p = Path(log_path)
    if not p.exists():
        return []
    events: list[dict] = []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue                    # 容忍半寫入的最後一行
        if isinstance(ev, dict):
            events.append(ev)
    return events


def summarize(events: list[dict]) -> dict:
    """事件序列 → 進度摘要（`progress.py` 的原始契約，欄位不變）。

    兩個容易誤判的細節：

    * **`current` 取自「任何帶 phase 的事件」，不只 `phase_start`。** `run_loop.py`
      只發 `loop_tick` / `tool_call` / `error`（都帶 `phase`），從不發 `phase_start`
      ——只認 `phase_start` 的話，Phase 6 修復迴圈跑一整輪畫面仍顯示 Phase 0。
      `phase_end` 除外：它代表「離開該 phase」，不該把游標留在那裡。
    * **`run_start` 會重置累加器。** events.jsonl 是 append-only 且跨 run 共用，
      不重置的話上一輪的 escalation 會被當成本輪的狀態一直顯示。
    """
    current: int | None = None
    completed: set[int] = set()
    iteration = 0
    cost = 0.0
    last_status: str | None = None
    failed_phases: set[int] = set()

    for ev in events:
        etype = ev.get("event_type")
        pnum = phase_num(ev.get("phase"))

        if etype == "run_start":
            # 新的一輪 run：把上一輪的狀態全部清掉，避免跨 run 汙染。
            current, completed, iteration, cost, last_status = None, set(), 0, 0.0, None
            failed_phases = set()
            continue

        if etype == "phase_end" and pnum is not None:
            if ev.get("status") == "success":
                completed.add(pnum)
                # 該 phase 最後是成功的 → 中途那些錯誤已經被收斂掉了。
                failed_phases.discard(pnum)
            elif ev.get("status") == "failure":
                failed_phases.add(pnum)
        elif pnum is not None:
            current = pnum

        if ev.get("iteration") is not None:
            iteration = ev["iteration"]
        if ev.get("cumulative_cost_usd") is not None:
            cost = ev["cumulative_cost_usd"]
        if ev.get("status"):
            last_status = ev["status"]
        if etype == "error":
            # 記在該 phase 名下。之前是一個全域 latch，於是 Phase 6 修復迴圈裡
            # 一次「已經被下一輪修好」的 escalation，會讓七階段全部跑完之後畫面
            # 仍然是紅的、還掛著早就過期的「錯誤：no_progress」。
            failed_phases.add(pnum if pnum is not None else _RUN_LEVEL_FAILURE)

    return {
        "current": current,
        "completed": completed,
        "iteration": iteration,
        "cost": cost,
        "last_status": last_status,
        "failed": bool(failed_phases),
        "failed_phases": failed_phases,
    }


def division_stats(events: list[dict]) -> dict:
    """Claude 規劃 vs Codex 實作的分工證據（OBS-R2 的 llm_call / tool_call）。

    語意**刻意與 `vscode-extension/dashboard.js` 的 `summarizeEvents` 逐項對齊**，
    好讓 `tests/tools/test_dashboard_parity.py` 能機械化證明兩邊一致——控制台是
    JS、progress/desktop 是 Python，沒有這道測試就只能靠人記得同步。

    注意這裡的「當前 phase」與 :func:`summarize` 的 ``current`` **不同**，是故意的：

    * `summarize` 的 ``current`` 取自任何帶 phase 的事件（要能在只有 loop_tick 時
      仍顯示正確進度條）。
    * 這裡的 ``reached5`` 只認 ``phase_start`` / ``phase_end``——「有沒有正式進入
      Phase 5」是要拿來發分工警示的，寧可保守，不能因為一個 loop_tick 就誤報
      「你只燒 Claude 沒用 Codex」。
    """
    claude = {"calls": 0, "in_tok": 0, "out_tok": 0}
    codex = {"calls": 0}
    codex_in_phase5 = 0
    completed: set[int] = set()
    current: int | None = None      # 只由 phase_start 設定（dashboard 語意）
    cur_phase: int | None = None    # phase_start / phase_end 都更新

    for ev in events:
        etype = ev.get("event_type")
        pnum = phase_num(ev.get("phase"))
        if etype == "phase_start" and pnum is not None:
            current = pnum
            cur_phase = pnum
        elif etype == "phase_end" and pnum is not None:
            if ev.get("status") == "success":
                completed.add(pnum)
            cur_phase = pnum
        elif etype == "llm_call":
            claude["calls"] += 1
            claude["in_tok"] += ev.get("gen_ai.usage.input_tokens") or 0
            claude["out_tok"] += ev.get("gen_ai.usage.output_tokens") or 0
        elif etype == "tool_call" and re.search(r"codex", str(ev.get("tool") or ""), re.I):
            codex["calls"] += 1
            if (pnum if pnum is not None else cur_phase) == 5:
                codex_in_phase5 += 1

    reached5 = (current is not None and current >= 5) or 5 in completed
    return {
        "claude": claude,
        "codex": codex,
        "codex_in_phase5": codex_in_phase5,
        # 已進入/完成 phase5 卻沒有任何 codex 呼叫 → 疑似「只燒 Claude、沒用 Codex 實作」
        "division_warning": bool(reached5 and codex_in_phase5 == 0),
    }


def _marker(summary: dict) -> int:
    """進度條位置：取「當前 phase」與「已完成最高 phase」較大者。

    即使某個 `phase_start` 漏接，進度也不會卡住（沿用 progress.py 的既有行為）。
    """
    done = summary["completed"]
    return max((summary["current"] or 0), (max(done) if done else 0))


def build_model(events: list[dict], *, log_exists: bool) -> dict:
    """事件序列 → 給 UI 消費的完整 JSON-safe 模型。

    比 :func:`summarize` 多出：每個 phase 的逐項狀態、pipeline 總狀態、
    錯誤清單、最後事件時間戳。所有值都是 JSON 可序列化的（`completed` 轉成 list）。
    """
    summary = summarize(events)
    marker = _marker(summary)
    done = summary["completed"]

    # 逐 phase 狀態：done / active / failed / pending
    phases = []
    for num in sorted(PHASES):
        if num in done:
            state = "done"
        elif summary["failed"] and num == marker:
            state = "failed"
        elif num == marker and log_exists:
            state = "active"
        else:
            state = "pending"
        phases.append({"num": num, "name": PHASES[num], "state": state})

    # 只收「最後一個 run_start 之後」的錯誤，理由同 summarize 的重置邏輯。
    scoped = events
    for i in range(len(events) - 1, -1, -1):
        if events[i].get("event_type") == "run_start":
            scoped = events[i + 1:]
            break

    errors = [
        {
            "phase": ev.get("phase"),
            "reason": ev.get("reason") or ev.get("status") or "unknown",
            "timestamp": ev.get("timestamp"),
        }
        for ev in scoped
        if ev.get("event_type") == "error"
        # 只留「那個 phase 到最後仍然是壞的」錯誤。修復迴圈中途 escalate、下一輪
        # 修好的那些，phase_end 成功時已從 failed_phases 移除——留著只會讓交付完成的
        # 畫面掛著一條早就過期的「最後錯誤：no_progress」。
        and (phase_num(ev.get("phase")) if ev.get("phase") is not None
             else _RUN_LEVEL_FAILURE) in summary["failed_phases"]
    ]

    if not log_exists:
        overall = STATE_NOT_STARTED
    elif summary["failed"]:
        overall = STATE_ESCALATED
    elif TOTAL in done:
        overall = STATE_DONE
    else:
        overall = STATE_RUNNING

    last_ts = None
    for ev in reversed(events):
        if ev.get("timestamp"):
            last_ts = ev["timestamp"]
            break

    return {
        "log_exists": log_exists,
        "state": overall,
        "total": TOTAL,
        "marker": marker,
        "current": summary["current"],
        "current_name": PHASES.get(marker, "?"),
        "completed": sorted(done),
        "phases": phases,
        "iteration": summary["iteration"],
        "cost_usd": summary["cost"],
        "last_status": summary["last_status"],
        "failed": summary["failed"],
        "errors": errors,
        "last_event_ts": last_ts,
        "event_count": len(events),
        **division_stats(scoped),
    }


def load_model(log_path: str | Path) -> dict:
    """便捷入口：讀檔 + 建模型（UI 端只需要這一個呼叫）。"""
    p = Path(log_path)
    return build_model(read_events(p), log_exists=p.exists())


def render_summary_lines(summary: dict, *, log_exists: bool,
                         errors: list[dict] | None = None) -> list[str]:
    """核心渲染器：吃 :func:`summarize` 的輸出，回傳人類可讀的行。

    `progress.py`（終端機進度條）與 `render_lines`（模型版）都走這裡，
    避免兩份渲染邏輯漂移。`summary["completed"]` 接受 set 或 list。
    """
    if not log_exists:
        return ["[CodexAutoAI] 尚未開始（找不到 log/events.jsonl）。",
                "在 Claude Code 裡打 /start 或描述需求即可啟動。"]

    done = sorted(summary["completed"])
    marker = max((summary["current"] or 0), (done[-1] if done else 0))
    bar = "".join("▓" if i <= marker else "░" for i in range(TOTAL + 1))

    if summary["failed"]:
        state_label = "✗ 失敗/升級"
    elif marker == TOTAL and TOTAL in done:
        state_label = "✓ 完成"
    else:
        state_label = "● 進行中"

    lines = [
        f"[CodexAutoAI] Phase {marker}/{TOTAL} {bar} {PHASES.get(marker, '?')}  {state_label}",
        f"            已完成階段：{done or '無'}",
    ]
    if summary["iteration"]:
        lines.append(f"            當前迭代：第 {summary['iteration']} 輪（守衛上限 3）")
    if summary["cost"]:
        lines.append(f"            累計成本：${summary['cost']:.4f} USD")
    if errors:
        last_err = errors[-1]
        lines.append(f"            最後錯誤：{last_err['phase']} / {last_err['reason']}")
    return lines


def render_lines(model: dict) -> list[str]:
    """把 :func:`build_model` 的模型渲染成人類可讀的行（面板 / CLI 共用）。"""
    summary = {
        "current": model["current"],
        "completed": model["completed"],
        "iteration": model["iteration"],
        "cost": model["cost_usd"],
        "failed": model["failed"],
    }
    return render_summary_lines(summary, log_exists=model["log_exists"],
                                errors=model["errors"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CodexAutoAI 事件模型（共用讀取層）")
    ap.add_argument("--log", default="log/events.jsonl",
                    help="事件日誌路徑（預設 log/events.jsonl）")
    ap.add_argument("--json", action="store_true",
                    help="輸出 JSON（給 desktop 面板 / VS Code extension 消費）")
    args = ap.parse_args(argv)

    # Windows 主控台預設 cp950 無法輸出進度條字元與中文，強制 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    model = load_model(args.log)
    if args.json:
        print(json.dumps(model, ensure_ascii=False))
    else:
        print("\n".join(render_lines(model)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
