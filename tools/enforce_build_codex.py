#!/usr/bin/env python3
"""
enforce_build_codex.py — PreToolUse 守門員：Phase 5（build）期間禁止 Claude 直接寫 src/。

CodexAutoAI 的核心不變式是「Claude 規劃、Codex 實作」（見 CLAUDE.md：你不直接寫程式碼）。
Phase 5 的 src/ 實作必須由 `codex exec --full-auto` 產生——Codex 透過自己的行程寫檔，
**不經 Claude 的 Edit/Write 工具層**，所以本 hook 看不到 Codex 的寫入；它只攔得到 Claude
自己的 Edit/Write/MultiEdit。因此「build 期間擋掉 Claude 對 src/ 的 Edit/Write」就等於
強制所有實作走 Codex。

判斷「正在 build」：讀 `log/state.json`，phase == "phase5" 且 completed_actions 不含
"phase5-end"（精準鎖定「build 進行中」；build 結束或其他 phase、無 run、框架自身開發都不擋）。

協定（Claude Code PreToolUse）：
  - 放行：exit 0、無輸出。
  - 擋下：exit 0 + {"hookSpecificOutput":{"hookEventName":"PreToolUse",
          "permissionDecision":"deny","permissionDecisionReason":"<文字>"}}。
  - **fail-open**：任何解析/IO 錯誤一律放行，絕不誤擋而卡死 pipeline。
  - 停用：設環境變數 CODEXAUTOAI_NO_BUILD_ENFORCE=1。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

_GUARDED_TOOLS = {"Edit", "Write", "MultiEdit"}

_DENY_REASON = (
    "CodexAutoAI 規則：Phase 5（build）期間 src/ 的實作必須由 Codex 產生，"
    "不可由 Claude 直接 Edit/Write/MultiEdit。\n"
    "請改用 function-builder 的方式呼叫：\n"
    "    codex exec --full-auto \"根據規格實作 {目標檔案} …\"\n"
    "（Codex 會直接寫檔，不經工具層。如需暫時停用此檢查："
    "設環境變數 CODEXAUTOAI_NO_BUILD_ENFORCE=1）"
)


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent


def _is_building(root: Path) -> bool:
    """phase == phase5 且尚未 phase5-end → build 進行中。讀不到 state 一律 False（放行）。"""
    try:
        st = json.loads((root / "log" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(st, dict):
        return False
    phase = str(st.get("phase") or "")
    completed = st.get("completed_actions") or []
    return phase == "phase5" and "phase5-end" not in completed


def _under_src(root: Path, file_path: str) -> bool:
    try:
        target = Path(file_path).resolve()
        src_root = (root / "src").resolve()
    except (OSError, ValueError):
        return False
    return target == src_root or src_root in target.parents


def evaluate(payload: dict, root: Path) -> Optional[str]:
    """回傳 deny 理由字串代表要擋；回傳 None 代表放行。純函式，方便測試。"""
    if (os.environ.get("CODEXAUTOAI_NO_BUILD_ENFORCE") or "").strip():
        return None
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool_name") or ""
    if tool not in _GUARDED_TOOLS:
        return None
    tin = payload.get("tool_input") or {}
    file_path = tin.get("file_path") or tin.get("path") or ""
    if not file_path:
        return None
    if not _is_building(root):
        return None
    if not _under_src(root, file_path):
        return None
    return _DENY_REASON


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:  # noqa: BLE001 — fail-open
        return 0
    try:
        reason = evaluate(payload, _project_dir())
    except Exception:  # noqa: BLE001 — fail-open
        reason = None
    if reason is not None:
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
