#!/usr/bin/env python3
"""
arm.py — autopilot 武裝 hook（UserPromptSubmit, matcher `^/autopilot`）。

把 `/autopilot on <task> | off | status` 在模型看到 prompt 之前攔下處理。
關鍵：用 **hook stdin 的 session_id** 綁定旗標（模型讀不到自己的 session_id），
per-session 一個檔 `log/autopilot/<session_id>.json` → 多 session 各自獨立、不互搶。

一律 exit 0、fail-open（沿用本 repo hook 慣例）。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

MAX_ITERATIONS = 30
_ON = re.compile(r"^\s*/autopilot\s+on\b\s*(.*)$", re.IGNORECASE | re.DOTALL)
_OFF = re.compile(r"^\s*/autopilot\s+off\b", re.IGNORECASE)
_STATUS = re.compile(r"^\s*/autopilot\s+status\b", re.IGNORECASE)


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent.parent


def _state_dir() -> Path:
    d = _project_dir() / "log" / "autopilot"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _emit_context(text: str) -> None:
    # UserPromptSubmit：純 stdout 會成為附加 context 給模型看。
    print(text)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0

    prompt = str(payload.get("prompt", ""))
    sid = str(payload.get("session_id", "")) or "nosession"
    sd = _state_dir()
    state_f = sd / f"{sid}.json"
    done_f = sd / f"{sid}.done"

    try:
        m = _ON.match(prompt)
        if m:
            task = m.group(1).strip()
            done_f.unlink(missing_ok=True)
            state_f.write_text(json.dumps({
                "session_id": sid, "iterations": 0, "max_iterations": MAX_ITERATIONS,
                "task": task, "started": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False), encoding="utf-8")
            _emit_context(
                f"[autopilot] 已開啟非停模式（上限 {MAX_ITERATIONS} 次續跑）。"
                "請自動推進到底；只有 Phase 2 需澄清、C6 不可逆操作、或守衛 escalate 才停。"
                f"任務：{task or '（沿用對話中的需求）'}")
            return 0
        if _OFF.match(prompt):
            state_f.unlink(missing_ok=True)
            done_f.unlink(missing_ok=True)
            _emit_context("[autopilot] 已關閉非停模式。")
            return 0
        if _STATUS.match(prompt):
            if state_f.exists():
                st = json.loads(state_f.read_text(encoding="utf-8"))
                _emit_context(f"[autopilot] 進行中：{st.get('iterations')}/{st.get('max_iterations')} 次，"
                             f"任務：{st.get('task')}")
            else:
                _emit_context("[autopilot] 未開啟。")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"autopilot-arm: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
