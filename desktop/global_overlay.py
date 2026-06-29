"""啟動期全域設定套用 / 還原（desktop App 與 VS Code extension 共用協定）。

CodexAutoAI 桌面 App / extension 啟動時，把「full-auto 友善」的設定**暫時**套到使用者
全域設定檔；關閉時自動還原。讓使用者開著 App 期間，任何地方跑的 Claude / Codex 都吃到
免確認的自動化設定，App 一關就回復原狀，不留痕跡。

套用範圍（依使用者決策）：
- Claude Code ``~/.claude/settings.json``：整個 ``permissions`` 區塊換成 bypassPermissions
  + allow/ask/deny 安全網（**只動 permissions，不碰 hooks / env / 其他鍵**）。
- Codex ``~/.codex/config.toml``：頂層 ``approval_policy`` / ``sandbox_mode`` 換成 full-auto
  友善值（用標記區塊插在檔首，只動這兩個頂層鍵）。

多實例安全（desktop + extension 可能同時開）：採 **owner 引用計數**。
state 檔 ``~/.codexautoai/overlay-state.json`` 記錄目前持有者與「原始值備份」。
第一個 acquire 的 owner 才真的套用並存備份；最後一個 release 的 owner 才還原。
每個 owner 綁自己的 PID，acquire 時清掉「PID 已死」或「逾期」的殭屍 owner，
所以即使 App crash 沒走到 release，下一次啟動也會把殘留設定收乾淨。

CLI（給測試 / 手動操作）::

    python global_overlay.py acquire <token>
    python global_overlay.py release <token>
    python global_overlay.py status

所有公開函式都不 raise——套用 / 還原失敗只記 log 並回報，絕不擋住 App 啟動或關閉。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATE_VERSION = 1
# 殭屍 owner 逾期保險：PID 存活檢查之外，再加一道時間上限（避免 PID 被回收誤判存活）。
OWNER_TTL_SEC = 24 * 60 * 60

# Claude Code 全域 permissions overlay（與專案 .claude/settings.json 一致的自動化設定）。
CLAUDE_PERMISSIONS: dict[str, Any] = {
    "defaultMode": "bypassPermissions",
    "allow": [
        "Bash(*)", "Read(*)", "Edit(*)", "Write(*)", "Glob(*)", "Grep(*)",
        "Agent(*)", "TodoWrite(*)", "WebFetch(*)", "WebSearch(*)", "Skill(*)",
    ],
    "ask": [
        "Bash(git commit:*)", "Bash(git push:*)", "Bash(git reset --hard:*)",
        "Bash(git clean:*)", "Bash(rm -rf:*)", "Bash(*deploy*)",
    ],
    "deny": [
        "Bash(rm -rf /*)", "Bash(rm -rf ~*)", "Bash(* | sh)",
        "Bash(curl * | bash)", "Bash(wget * | bash)", "Bash(mkfs*)",
        "Bash(dd if=*)",
    ],
}

# Codex full-auto 友善頂層鍵（對應 launcher 用的 codex exec --full-auto）。
CODEX_KEYS: dict[str, str] = {
    "approval_policy": "on-failure",
    "sandbox_mode": "workspace-write",
}
_CODEX_BEGIN = "# >>> codexautoai overlay (auto, do not edit) >>>"
_CODEX_END = "# <<< codexautoai overlay <<<"
_CODEX_KEY_RE = re.compile(
    r"^\s*(" + "|".join(map(re.escape, CODEX_KEYS)) + r")\s*=", re.IGNORECASE
)


# ── 路徑（全部走 Path.home() → 測試可 monkeypatch）─────────────────────────────
def state_path() -> Path:
    return Path.home() / ".codexautoai" / "overlay-state.json"


def claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


# ── PID 存活 ──────────────────────────────────────────────────────────────────
def _pid_alive(pid: int) -> bool:
    """跨平台判斷 PID 是否存活。判不準時保守回 True（寧可不誤刪 owner）。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if handle:
                # 還要確認不是「已結束但 handle 還在」：取結束碼，259(STILL_ACTIVE) 才算活著。
                exit_code = ctypes.c_ulong()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                kernel32.CloseHandle(handle)
                if ok:
                    return exit_code.value == 259  # STILL_ACTIVE
                return True
            # 開不了：5(ERROR_ACCESS_DENIED) 代表存在但無權；其餘多半是不存在。
            return kernel32.GetLastError() == 5
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


# ── state 讀寫 ────────────────────────────────────────────────────────────────
def _empty_state() -> dict:
    return {"version": STATE_VERSION, "owners": {}, "backup": None}


def _load_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and "owners" in data:
            data.setdefault("backup", None)
            data.setdefault("owners", {})
            return data
    except (OSError, ValueError):
        pass
    return _empty_state()


def _save_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _prune_owners(state: dict) -> None:
    """清掉 PID 已死或逾期的殭屍 owner（in-place）。"""
    now = time.time()
    live = {}
    for token, meta in (state.get("owners") or {}).items():
        meta = meta or {}
        pid = int(meta.get("pid") or 0)
        ts = float(meta.get("ts") or 0)
        if (now - ts) > OWNER_TTL_SEC:
            continue
        if pid and not _pid_alive(pid):
            continue
        live[token] = meta
    state["owners"] = live


# ── Claude 套用 / 還原（key-level，只動 permissions）──────────────────────────
def _apply_claude() -> dict:
    """套用 permissions overlay，回傳還原所需的備份描述。"""
    p = claude_settings_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"permissions": CLAUDE_PERMISSIONS},
                                ensure_ascii=False, indent=2), encoding="utf-8")
        return {"file_absent": True}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("settings.json 非物件")
    except (OSError, ValueError) as exc:
        logger.warning("讀取 Claude settings 失敗，略過套用：%s", exc)
        return {"skipped": True}
    backup: dict = {"file_absent": False, "had_permissions": "permissions" in data}
    if "permissions" in data:
        backup["permissions"] = data["permissions"]
    data["permissions"] = CLAUDE_PERMISSIONS
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup


def _revert_claude(backup: Optional[dict]) -> None:
    if not backup or backup.get("skipped"):
        return
    p = claude_settings_path()
    if backup.get("file_absent"):
        # 檔案本來不存在：移除我們加的 permissions；若移完是空物件就刪檔。
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            data.pop("permissions", None)
            if data:
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            elif p.exists():
                p.unlink()
        return
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
    except (OSError, ValueError):
        return
    if backup.get("had_permissions"):
        data["permissions"] = backup.get("permissions")
    else:
        data.pop("permissions", None)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Codex 套用 / 還原（標記區塊，只動兩個頂層鍵）─────────────────────────────
def _codex_block() -> str:
    lines = [_CODEX_BEGIN]
    lines += [f'{k} = "{v}"' for k, v in CODEX_KEYS.items()]
    lines.append(_CODEX_END)
    return "\n".join(lines)


def _strip_codex_block(text: str) -> str:
    """移除既有 overlay 標記區塊（含區塊後緊鄰的一個空行），保留其餘內容。"""
    out, skipping = [], False
    for line in text.splitlines():
        if line.strip() == _CODEX_BEGIN:
            skipping = True
            continue
        if skipping:
            if line.strip() == _CODEX_END:
                skipping = False
            continue
        out.append(line)
    return "\n".join(out)


def _split_top_region(lines: list[str]) -> int:
    """回傳第一個 table header（``[...]``）的索引；沒有則回 len(lines)。
    該索引之前才是頂層作用域，移除頂層 dup 鍵只在這段做。"""
    for i, line in enumerate(lines):
        if line.lstrip().startswith("["):
            return i
    return len(lines)


def _apply_codex() -> dict:
    """把 full-auto 友善頂層鍵插到 config.toml 檔首，回傳備份描述。"""
    p = codex_config_path()
    block = _codex_block()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(block + "\n", encoding="utf-8")
        return {"file_absent": True, "removed_lines": []}
    try:
        original = p.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("讀取 Codex config 失敗，略過套用：%s", exc)
        return {"skipped": True}
    cleaned = _strip_codex_block(original)
    lines = cleaned.splitlines()
    cut = _split_top_region(lines)
    # 只在頂層作用域（首個 table 之前）移除我們會接管的 dup 鍵，並記下原始行供還原。
    removed = [ln for ln in lines[:cut] if _CODEX_KEY_RE.match(ln)]
    kept_top = [ln for ln in lines[:cut] if not _CODEX_KEY_RE.match(ln)]
    rest = lines[cut:]
    new_lines = [block, ""] + kept_top + rest
    p.write_text("\n".join(new_lines).rstrip("\n") + "\n", encoding="utf-8")
    return {"file_absent": False, "removed_lines": removed}


def _revert_codex(backup: Optional[dict]) -> None:
    if not backup or backup.get("skipped"):
        return
    p = codex_config_path()
    if not p.exists():
        return
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return
    cleaned = _strip_codex_block(text)
    if backup.get("file_absent"):
        # 檔案本來不存在：拿掉區塊後若沒有實質內容就刪檔。
        if cleaned.strip():
            p.write_text(cleaned.rstrip("\n") + "\n", encoding="utf-8")
        else:
            p.unlink()
        return
    # 還原被移除的頂層原始鍵：插回檔首（頂層作用域）。
    removed = backup.get("removed_lines") or []
    lines = cleaned.splitlines()
    cut = _split_top_region(lines)
    new_lines = list(removed) + lines[:cut] + lines[cut:] if removed else lines
    p.write_text("\n".join(new_lines).rstrip("\n") + "\n", encoding="utf-8")


# ── 公開 API ─────────────────────────────────────────────────────────────────
def _disabled() -> bool:
    return bool((os.environ.get("CODEXAUTOAI_NO_GLOBAL_OVERLAY") or "").strip())


def acquire(token: str) -> dict:
    """登記一個 owner；若是第一個就套用全域 overlay 並存備份。永不 raise。"""
    if _disabled():
        return {"ok": False, "applied": False, "reason": "disabled"}
    try:
        state = _load_state()
        _prune_owners(state)
        first = not state["owners"]
        if first and not state.get("backup"):
            try:
                state["backup"] = {
                    "claude": _apply_claude(),
                    "codex": _apply_codex(),
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("套用全域 overlay 失敗：%s", exc)
                state["backup"] = None
        state["owners"][token] = {"pid": os.getpid(), "ts": time.time()}
        _save_state(state)
        return {"ok": True, "applied": first, "owners": len(state["owners"])}
    except Exception as exc:  # noqa: BLE001
        logger.warning("acquire 失敗：%s", exc)
        return {"ok": False, "applied": False, "reason": str(exc)}


def release(token: str) -> dict:
    """移除一個 owner；若清空就還原全域 overlay。永不 raise。"""
    try:
        state = _load_state()
        state["owners"].pop(token, None)
        _prune_owners(state)
        reverted = False
        if not state["owners"]:
            backup = state.get("backup") or {}
            try:
                _revert_claude(backup.get("claude"))
                _revert_codex(backup.get("codex"))
                reverted = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("還原全域 overlay 失敗：%s", exc)
            state["backup"] = None
            # owners 清空：state 檔可整個刪掉，保持乾淨。
            try:
                if state_path().exists():
                    state_path().unlink()
            except OSError:
                _save_state(state)
            return {"ok": True, "reverted": reverted, "owners": 0}
        _save_state(state)
        return {"ok": True, "reverted": reverted, "owners": len(state["owners"])}
    except Exception as exc:  # noqa: BLE001
        logger.warning("release 失敗：%s", exc)
        return {"ok": False, "reverted": False, "reason": str(exc)}


def status() -> dict:
    state = _load_state()
    _prune_owners(state)
    return {
        "active": bool(state["owners"]),
        "owners": list(state["owners"].keys()),
        "applied": state.get("backup") is not None,
    }


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: global_overlay.py {acquire|release|status} [token]")
        return 2
    cmd = argv[0]
    if cmd == "status":
        print(json.dumps(status(), ensure_ascii=False))
        return 0
    token = argv[1] if len(argv) > 1 else f"cli:{os.getpid()}"
    if cmd == "acquire":
        print(json.dumps(acquire(token), ensure_ascii=False))
        return 0
    if cmd == "release":
        print(json.dumps(release(token), ensure_ascii=False))
        return 0
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
