#!/usr/bin/env python3
"""
cont.py — autopilot 續跑 hook（Stop event）。

當模型想結束回合時觸發。若本 session 開了 autopilot 且尚未完成，輸出
{"decision":"block","reason":...} 把模型擋回去繼續做（Claude Code Stop 協定）。

五道安全閥（借鏡 autopilot，per-session 版）：
  1. stop_hook_active（Claude 自己的續跑已在跑）→ 放行
  2. 本 session 無 state 檔（沒開 autopilot）→ 放行
  3. <sid>.done 存在（任務完成）→ 清檔放行
  4. 迭代達上限 → 清檔放行（防無限循環）
  5. 否則 iterations+1、回寫、block 續跑

per-session 檔 → 多 session 各自獨立。一律 exit 0。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REASON = (
    "繼續自動推進 CodexAutoAI pipeline 到下一個 Phase，不要在此停下等我。"
    "只有以下情況可停（停前先建立 done sentinel：在 log/autopilot/<session_id>.done 建一個空檔）：\n"
    "(a) Phase 2 需求需要我澄清；(b) C6 不可逆操作（commit/push/刪除）待我授權；"
    "(c) 終止守衛 escalate。否則請接著做。"
)


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent.parent


def _state_dir() -> Path:
    return _project_dir() / "log" / "autopilot"


def _block(reason: str) -> None:
    json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0  # fail-open：解析失敗 → 正常停（不誤擋）

    # 閥 1：Claude 自己的續跑迴圈已在進行
    if payload.get("stop_hook_active") is True:
        return 0

    sid = str(payload.get("session_id", "")) or "nosession"
    sd = _state_dir()
    state_f = sd / f"{sid}.json"
    done_f = sd / f"{sid}.done"

    # 閥 2：本 session 沒開 autopilot
    if not state_f.exists():
        return 0

    try:
        st = json.loads(state_f.read_text(encoding="utf-8"))
    except Exception:
        return 0

    # 閥 3：完成 sentinel
    if done_f.exists():
        state_f.unlink(missing_ok=True)
        done_f.unlink(missing_ok=True)
        return 0

    # 閥 4：迭代上限
    it = int(st.get("iterations", 0))
    mx = int(st.get("max_iterations", 30))
    if it >= mx:
        state_f.unlink(missing_ok=True)
        print(f"[autopilot] 已達續跑上限 {mx} 次，自動停止。", file=sys.stderr)
        return 0

    # 閥 5：續跑
    st["iterations"] = it + 1
    try:
        state_f.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    _block(REASON)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
