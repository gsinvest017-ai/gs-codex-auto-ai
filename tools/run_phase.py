#!/usr/bin/env python3
"""
run_phase.py — Stage 1：phase 邊界事件橋接器（把 v2 EventBus 接進運行時）。

Dispatcher 在每個 phase 邊界呼叫本 CLI，由 v2 的 `EventBus` 確定性寫入單一檔
`log/events.jsonl`（正是 `tools/progress.py` 與 `tools/dispatch_hook.py` 讀的檔）。
這讓進度儀表板在真實 run 中真的會動，並讓 run_id 跨 phase（跨獨立行程）持久化。

用法：
    python tools/run_phase.py start [--run-id ID]            # mint/記錄 run_id，印出
    python tools/run_phase.py begin --phase N [--run-id ID]  # emit phase_start
    python tools/run_phase.py end --phase N --status success|failure [--error NAME] [--run-id ID]
    python tools/run_phase.py status                         # 印目前 run_id

設計原則（沿用 dispatch_hook 的 fail-open）：
  - 一律 exit 0；logging bridge 永不 crash pipeline。
  - 時間戳一律由 EventBus 的系統時鐘產生（C3），呼叫端不傳 timestamp。
  - 重複 begin/end 無害（progress.summarize 是 set / last-write-wins）。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path


# 框架原始碼根目錄：固定為本工具的上層（tools/ 的 parent），與 log 位置無關。
_TOOL_ROOT = Path(__file__).resolve().parent.parent


def _project_dir() -> Path:
    """log/ 等輸出的根目錄；可由 CLAUDE_PROJECT_DIR 覆寫（測試/多專案用）。"""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else _TOOL_ROOT


def _paths(root: Path) -> dict:
    log = root / "log"
    return {
        "events": str(log / "events.jsonl"),
        "audit": str(log / "audit.jsonl"),
        "state": str(log / "state.json"),
        "run_ptr": log / "current_run.txt",
    }


def _mint_run_id() -> str:
    # C3：系統時鐘；非 LLM 自填。
    return "run-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def _resolve_run_id(paths: dict, explicit: str | None) -> str:
    """優先序：--run-id 旗標 > current_run.txt > mint 新 id（fail-safe）。"""
    if explicit:
        return explicit
    ptr: Path = paths["run_ptr"]
    try:
        if ptr.exists():
            val = ptr.read_text(encoding="utf-8").strip()
            if val:
                return val
    except Exception:
        pass
    return _mint_run_id()


def _write_ptr(paths: dict, run_id: str) -> None:
    ptr: Path = paths["run_ptr"]
    ptr.parent.mkdir(parents=True, exist_ok=True)
    ptr.write_text(run_id + "\n", encoding="utf-8")


def _build_orch(paths: dict, run_id: str):
    # 延遲匯入：sys.path 加「框架原始碼根」（非 log 根），匯入路徑同 tests（src.codexautoai_v2.*）。
    if str(_TOOL_ROOT) not in sys.path:
        sys.path.insert(0, str(_TOOL_ROOT))
    from src.codexautoai_v2.orchestrator import Orchestrator  # noqa: E402
    return Orchestrator(
        event_path=paths["events"],
        audit_path=paths["audit"],
        state_path=paths["state"],
        run_id=run_id,
    )


def _phase_label(n: str) -> str:
    s = str(n).lower()
    return s if s.startswith("phase") else f"phase{s}"


def cmd_start(root: Path, run_id: str | None) -> str:
    paths = _paths(root)
    rid = run_id or _mint_run_id()
    _write_ptr(paths, rid)
    orch = _build_orch(paths, rid)
    orch.state.checkpoint(paths["state"])
    print(rid)
    return rid


def cmd_begin(root: Path, phase: str, run_id: str | None) -> None:
    paths = _paths(root)
    rid = _resolve_run_id(paths, run_id)
    _write_ptr(paths, rid)
    orch = _build_orch(paths, rid)
    label = _phase_label(phase)
    orch.state.set_phase(label)
    orch.events.emit("phase_start", phase=label, status="in_progress")
    orch.audit.append({"event": "phase_start", "phase": label})
    orch.state.checkpoint(paths["state"])


def cmd_end(root: Path, phase: str, status: str, error: str | None, run_id: str | None) -> None:
    paths = _paths(root)
    rid = _resolve_run_id(paths, run_id)
    orch = _build_orch(paths, rid)
    label = _phase_label(phase)
    fields = {"phase": label, "status": status}
    if error:
        fields["error"] = error
    orch.events.emit("phase_end", **fields)
    orch.audit.append({"event": "phase_end", "phase": label, "status": status})
    orch.state.mark_done(f"{label}-end")
    orch.state.checkpoint(paths["state"])


def cmd_status(root: Path) -> None:
    paths = _paths(root)
    ptr: Path = paths["run_ptr"]
    print(ptr.read_text(encoding="utf-8").strip() if ptr.exists() else "(no active run)")


def cmd_resume(root: Path) -> dict:
    """讀 state.json 回報可續跑點（STATE-R1）：run_id / 最後 phase / 已完成 action。
    Dispatcher / /start 可據此從中斷的 phase 續跑，而非從 Phase 0 重來。"""
    paths = _paths(root)
    info = {"resumable": False, "run_id": None, "phase": None, "completed": []}
    try:
        if str(_TOOL_ROOT) not in sys.path:
            sys.path.insert(0, str(_TOOL_ROOT))
        from src.codexautoai_v2.state import RunState  # noqa: E402
        st = RunState.load(paths["state"])
        info.update(resumable=True, run_id=st.run_id, phase=st.phase,
                    completed=st.completed_actions())
    except Exception:
        pass
    import json as _json
    print(_json.dumps(info, ensure_ascii=False))
    return info


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="CodexAutoAI phase 邊界事件橋接器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start"); p_start.add_argument("--run-id")
    p_begin = sub.add_parser("begin"); p_begin.add_argument("--phase", required=True); p_begin.add_argument("--run-id")
    p_end = sub.add_parser("end")
    p_end.add_argument("--phase", required=True)
    p_end.add_argument("--status", choices=["success", "failure"], required=True)
    p_end.add_argument("--error"); p_end.add_argument("--run-id")
    sub.add_parser("status")
    sub.add_parser("resume")

    args = ap.parse_args(argv)
    root = _project_dir()

    # fail-open：任何錯誤印 stderr 後仍 exit 0，絕不 crash pipeline。
    try:
        if args.cmd == "start":
            cmd_start(root, args.run_id)
        elif args.cmd == "begin":
            cmd_begin(root, args.phase, args.run_id)
        elif args.cmd == "end":
            cmd_end(root, args.phase, args.status, args.error, args.run_id)
        elif args.cmd == "status":
            cmd_status(root)
        elif args.cmd == "resume":
            cmd_resume(root)
    except Exception as exc:  # noqa: BLE001
        print(f"run_phase: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
