#!/usr/bin/env python3
"""
enforce_build_codex.py — PreToolUse 守門員：Phase 3–7 期間禁止 Claude 直接寫
src/、tests/、docs/（Codex-first 硬分工）。

CodexAutoAI 的核心不變式是「Claude 規劃、Codex 實作」（見 CLAUDE.md：你不直接寫程式碼）。
內容產出必須由 `codex exec --full-auto` 產生——Codex 透過自己的行程寫檔，
**不經 Claude 的 Edit/Write 工具層**，所以本 hook 看不到 Codex 的寫入；它只攔得到 Claude
自己的 Edit/Write/MultiEdit。因此「擋掉 Claude 對這些目錄的 Edit/Write」就等於
強制所有實作走 Codex。

守門範圍見下方常數：`_ENFORCED_PHASES`（phase3–phase7）、`_GUARDED_DIRS`
（src/tests/docs）、`_WHITELIST_FILES`（Phase 2 的規劃產物，屬 Claude 職責）。
Phase 0–2 是規劃期，不擋。

判斷「正在 build」：讀 `log/state.json`，phase 落在 `_ENFORCED_PHASES` 且
completed_actions 不含 "phase5-end"（build 結束、無 run、框架自身開發都不擋）。

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

# Codex-first 硬分工（CLAUDE.md）：Phase 3–7 進行中，內容產出（src/tests/docs）一律 Codex。
_ENFORCED_PHASES = {"phase3", "phase4", "phase5", "phase6", "phase7"}
_GUARDED_DIRS = ("src", "tests", "docs")
# 白名單：Phase 2 的規劃產物（Claude high-level planning 允許寫/更新）
_WHITELIST_FILES = {"requirements-spec.md"}

_DENY_REASON = (
    "CodexAutoAI Codex-first 規則：Phase 3–7 期間 src/、tests/、docs/ 的內容產出"
    "必須由 Codex 產生，不可由 Claude 直接 Edit/Write/MultiEdit。\n"
    "請改用：\n"
    "    codex exec --full-auto \"根據規格產出/修正 {目標檔案} …\"\n"
    "（Codex 會直接寫檔，不經工具層。如需暫時停用此檢查："
    "設環境變數 CODEXAUTOAI_NO_BUILD_ENFORCE=1）"
)

# 非停原則：pipeline 進行中不可把選擇丟回使用者（AskUserQuestion 會卡住等輸入，
# 控制台/autopilot 的無人值守流程會就此停擺）。
_DENY_ASK_REASON = (
    "CodexAutoAI 非停規則：pipeline 進行中不可用 AskUserQuestion 問使用者。\n"
    "請自行決定：選擇「最保守、最符合 docs/requirements-spec.md」的選項，"
    "把該決定與理由記到 log/（一行即可），然後直接繼續。\n"
    "（如需暫時停用：設環境變數 CODEXAUTOAI_NO_BUILD_ENFORCE=1）"
)


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent


def _is_building(root: Path) -> bool:
    """phase ∈ {phase3..phase7} 且該 phase 尚未 *-end → Codex-first 強制中。
    讀不到 state 一律 False（放行）。"""
    try:
        st = json.loads((root / "log" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(st, dict):
        return False
    phase = str(st.get("phase") or "")
    completed = st.get("completed_actions") or []
    return phase in _ENFORCED_PHASES and f"{phase}-end" not in completed


def _run_active(root: Path) -> bool:
    """任一 phase 進行中（phase 非空且未 *-end）→ pipeline 執行中。讀不到 state 放行。"""
    try:
        st = json.loads((root / "log" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(st, dict):
        return False
    phase = str(st.get("phase") or "")
    completed = st.get("completed_actions") or []
    return bool(phase) and f"{phase}-end" not in completed


def _under_src(root: Path, file_path: str) -> bool:
    """目標是否落在受管目錄（src/tests/docs）下；白名單檔案放行。"""
    try:
        target = Path(file_path).resolve()
    except (OSError, ValueError):
        return False
    if target.name in _WHITELIST_FILES:
        return False
    for d in _GUARDED_DIRS:
        try:
            guarded = (root / d).resolve()
        except (OSError, ValueError):
            continue
        if target == guarded or guarded in target.parents:
            return True
    return False


def evaluate(payload: dict, root: Path) -> Optional[str]:
    """回傳 deny 理由字串代表要擋；回傳 None 代表放行。純函式，方便測試。"""
    if (os.environ.get("CODEXAUTOAI_NO_BUILD_ENFORCE") or "").strip():
        return None
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool_name") or ""
    # 非停：pipeline 進行中禁止把選擇丟回使用者
    if tool == "AskUserQuestion":
        return _DENY_ASK_REASON if _run_active(root) else None
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
