#!/usr/bin/env python3
"""
launcher.py — CodexAutoAI 桌面啟動器（tkinter，純標準庫）。

給完全不懂指令的人：點桌面圖示 → 看環境是否就緒 → 一鍵「設定/修復」把能自動的跑掉 →
在輸入框打一句需求按「啟動」→ 自動開新終端機跑互動式 claude，完整七階段開始。

App 取代不了 claude / codex / node 這些 CLI 本身（登入要開瀏覽器），所以這裡只做：
偵測缺什麼、把能自動的自動化（呼叫 repo 的 setup.cmd）、其餘明確引導，全綠才啟用「啟動」。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

# ── GS 暗金主題 ──────────────────────────────────────────────────────────────
BG = "#0f1115"          # warm-black
CARD = "#171a21"
GOLD = "#d4af37"
CHAMPAGNE = "#e7ddc7"
GREEN = "#3fb950"
RED = "#f85149"
MUTED = "#8b949e"


def app_dir() -> Path:
    """框架檔（.claude/ CLAUDE.md tools/ setup.*）所在目錄。
    凍結（PyInstaller）時 exe 與框架檔同目錄；未凍結時取 repo 根（desktop/ 的 parent）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_DIR = app_dir()
IS_WIN = os.name == "nt"


# ── 環境偵測 ────────────────────────────────────────────────────────────────
def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: str, timeout: int = 10) -> tuple[int, str]:
    # shell=True：codex/claude 在 Windows 是 .cmd 蓋子，list 形式不帶 shell 會 FileNotFoundError。
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def check_claude() -> tuple[bool, str]:
    if not _which("claude"):
        return False, "未安裝 — 請安裝 Claude Code CLI"
    cred = Path.home() / ".claude" / ".credentials.json"
    if cred.exists():
        return True, "已安裝且已登入"
    return False, "已安裝但未登入 — 按「設定/修復」"


def check_codex() -> tuple[bool, str]:
    if not _which("codex"):
        return False, "未安裝 — 按「設定/修復」自動安裝"
    rc, _ = _run("codex login status")
    return (rc == 0, "已安裝且已登入" if rc == 0 else "已安裝但未登入 — 按「設定/修復」")


def check_simple(name: str, label: str) -> tuple[bool, str]:
    p = _which(name)
    return (bool(p), f"已安裝（{p}）" if p else f"未安裝 — 需要 {label}")


def gather_checks() -> list[dict]:
    claude_ok, claude_msg = check_claude()
    codex_ok, codex_msg = check_codex()
    node_ok, node_msg = check_simple("node", "Node.js（Codex 需要）")
    git_ok, git_msg = check_simple("git", "Git")
    py_ok, py_msg = check_simple("python", "Python 3.11+") if not getattr(sys, "frozen", False) else (True, "內建於 App")
    return [
        {"key": "claude", "name": "Claude Code", "ok": claude_ok, "msg": claude_msg, "critical": True},
        {"key": "codex", "name": "OpenAI Codex", "ok": codex_ok, "msg": codex_msg, "critical": True},
        {"key": "node", "name": "Node.js", "ok": node_ok, "msg": node_msg, "critical": False},
        {"key": "git", "name": "Git", "ok": git_ok, "msg": git_msg, "critical": False},
        {"key": "python", "name": "Python", "ok": py_ok, "msg": py_msg, "critical": False},
    ]


# ── 動作 ────────────────────────────────────────────────────────────────────
def run_setup() -> None:
    """開終端機跑 repo 既有的 setup（登入 + 裝 Codex + 啟用 hooks）。"""
    if IS_WIN:
        setup = APP_DIR / "setup.cmd"
        if setup.exists():
            subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k", str(setup)],
                            cwd=str(APP_DIR))
            return
    sh = APP_DIR / "setup.sh"
    if sh.exists():
        subprocess.Popen(["bash", str(sh)], cwd=str(APP_DIR))
    else:
        messagebox.showerror("CodexAutoAI", f"找不到 setup 腳本於 {APP_DIR}")


def launch_claude(requirement: str) -> bool:
    """開新終端機在 app 目錄跑互動式 claude，需求當初始 prompt。"""
    if not _which("claude"):
        messagebox.showerror("CodexAutoAI", "找不到 claude，請先按「設定/修復」安裝並登入。")
        return False
    req = (requirement or "").strip().replace('"', "'")
    try:
        if IS_WIN:
            inner = f'claude "{req}"' if req else "claude"
            if _which("wt"):   # 優先 Windows Terminal
                subprocess.Popen(f'wt -d "{APP_DIR}" cmd /k {inner}', shell=True)
            else:
                subprocess.Popen(f'start "CodexAutoAI" cmd /k {inner}',
                                cwd=str(APP_DIR), shell=True)
        else:
            cmd = f'claude "{req}"' if req else "claude"
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                if _which(term):
                    subprocess.Popen([term, "-e", "bash", "-lc", f"cd '{APP_DIR}' && {cmd}"])
                    break
            else:
                subprocess.Popen(["bash", "-lc", f"cd '{APP_DIR}' && {cmd}"])
        return True
    except Exception as exc:  # noqa: BLE001
        messagebox.showerror("CodexAutoAI", f"啟動失敗：{exc}")
        return False


# ── GUI ─────────────────────────────────────────────────────────────────────
class LauncherUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("CodexAutoAI")
        root.configure(bg=BG)
        root.geometry("560x560")
        root.minsize(520, 520)
        ico = APP_DIR / "desktop" / "codexautoai.ico"
        if not ico.exists():
            ico = APP_DIR / "codexautoai.ico"
        try:
            if ico.exists() and IS_WIN:
                root.iconbitmap(str(ico))
        except Exception:
            pass

        self.h1 = tkfont.Font(family="Segoe UI", size=20, weight="bold")
        self.h2 = tkfont.Font(family="Segoe UI", size=11)
        self.mono = tkfont.Font(family="Consolas", size=10)
        self.rows: dict[str, dict] = {}
        self._build()
        self.refresh()

    def _build(self) -> None:
        tk.Label(self.root, text="CodexAutoAI", font=self.h1, fg=GOLD, bg=BG).pack(pady=(22, 2))
        tk.Label(self.root, text="一句話描述需求，自動跑完需求→架構→寫碼→測試→交付",
                 font=self.h2, fg=MUTED, bg=BG).pack(pady=(0, 16))

        # 環境檢查卡
        card = tk.Frame(self.root, bg=CARD)
        card.pack(fill="x", padx=22)
        tk.Label(card, text="環境檢查", font=self.h2, fg=CHAMPAGNE, bg=CARD).pack(anchor="w", padx=14, pady=(12, 6))
        for c in gather_checks():
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", padx=14, pady=2)
            dot = tk.Label(row, text="●", font=self.h2, fg=MUTED, bg=CARD, width=2)
            dot.pack(side="left")
            name = tk.Label(row, text=c["name"], font=self.h2, fg=CHAMPAGNE, bg=CARD, width=12, anchor="w")
            name.pack(side="left")
            msg = tk.Label(row, text="", font=self.mono, fg=MUTED, bg=CARD, anchor="w")
            msg.pack(side="left", fill="x", expand=True)
            self.rows[c["key"]] = {"dot": dot, "msg": msg, "critical": c["critical"]}

        btns = tk.Frame(card, bg=CARD)
        btns.pack(fill="x", padx=14, pady=(8, 12))
        tk.Button(btns, text="🔧 設定 / 修復", command=self.on_setup, font=self.h2,
                  bg="#21262d", fg=CHAMPAGNE, relief="flat", padx=12, pady=4).pack(side="left")
        tk.Button(btns, text="↻ 重新檢查", command=self.refresh, font=self.h2,
                  bg="#21262d", fg=CHAMPAGNE, relief="flat", padx=12, pady=4).pack(side="left", padx=8)

        # 需求 + 啟動
        tk.Label(self.root, text="你想做什麼？", font=self.h2, fg=CHAMPAGNE, bg=BG).pack(anchor="w", padx=24, pady=(18, 4))
        self.req = tk.Text(self.root, height=3, font=self.h2, bg=CARD, fg=CHAMPAGNE,
                          insertbackground=GOLD, relief="flat", wrap="word")
        self.req.pack(fill="x", padx=22)
        self.req.insert("1.0", "做一個記帳 CLI 工具，資料存 SQLite")

        self.launch_btn = tk.Button(self.root, text="🚀 啟動 CodexAutoAI", command=self.on_launch,
                                    font=self.h1, bg=GOLD, fg=BG, relief="flat", pady=8)
        self.launch_btn.pack(fill="x", padx=22, pady=18)

        self.status = tk.Label(self.root, text="", font=self.mono, fg=MUTED, bg=BG)
        self.status.pack()

    def refresh(self) -> None:
        ready = True
        for c in gather_checks():
            r = self.rows[c["key"]]
            r["dot"].config(fg=GREEN if c["ok"] else (RED if c["critical"] else GOLD))
            r["msg"].config(text=c["msg"])
            if c["critical"] and not c["ok"]:
                ready = False
        if ready:
            self.launch_btn.config(state="normal", bg=GOLD)
            self.status.config(text="✓ 環境就緒，可以啟動", fg=GREEN)
        else:
            self.launch_btn.config(state="disabled", bg="#3a3a3a")
            self.status.config(text="請先按「設定 / 修復」完成 Claude / Codex 登入", fg=RED)

    def on_setup(self) -> None:
        run_setup()
        self.status.config(text="設定視窗已開啟，完成後請按「↻ 重新檢查」", fg=GOLD)

    def on_launch(self) -> None:
        req = self.req.get("1.0", "end").strip()
        if launch_claude(req):
            self.status.config(text="已開啟終端機，CodexAutoAI 在新視窗執行中…", fg=GREEN)


def main() -> int:
    root = tk.Tk()
    LauncherUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
