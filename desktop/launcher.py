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
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

try:
    import updater  # 版本檢查 / 自動更新（sibling module，凍結時一併打包）
except Exception:  # noqa: BLE001 — 缺模組不該擋住啟動器
    updater = None

try:
    import global_overlay  # 啟動套用 / 關閉還原全域 Claude/Codex 設定（sibling module）
except Exception:  # noqa: BLE001 — 缺模組不該擋住啟動器
    global_overlay = None

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


def load_progress_model(log_path: Path) -> dict | None:
    """讀 `log/events.jsonl` 的進度模型（共用 `tools/events_model.py`）。

    刻意**每次呼叫時才解析模組**而非 top-level import：`tools/` 不是套件、
    且凍結（PyInstaller）後的佈局與 repo 佈局不同，寫死 import 會在其中一種
    情境下壞掉。找不到模組就回 None，進度卡降級顯示、絕不擋住啟動器。

    VS Code extension 走 `python tools/events_model.py --json` 讀同一份模型，
    兩邊 UI 因此不會對「跑到哪」有不同說法。
    """
    try:
        import importlib.util
        mod_path = APP_DIR / "tools" / "events_model.py"
        if not mod_path.exists():
            return None
        spec = importlib.util.spec_from_file_location("events_model", mod_path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["events_model"] = mod      # dataclass/typing 解析需要
        spec.loader.exec_module(mod)
        return mod.load_model(log_path)
    except Exception:  # noqa: BLE001 — 進度顯示不該成為新的故障點
        return None


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


def check_gh() -> tuple[bool, str]:
    """GitHub CLI 登入狀態（與 extension checkGh 一致）。自動更新 / private repo 需要，非關鍵。"""
    if not _which("gh"):
        return False, "未安裝 — 自動更新需要（選用）"
    rc, _ = _run("gh auth status")
    return (rc == 0, "已安裝且已登入" if rc == 0 else "已安裝但未登入 — 按「設定/修復」")


def check_simple(name: str, label: str) -> tuple[bool, str]:
    p = _which(name)
    return (bool(p), f"已安裝（{p}）" if p else f"未安裝 — 需要 {label}")


def gather_checks() -> list[dict]:
    claude_ok, claude_msg = check_claude()
    codex_ok, codex_msg = check_codex()
    node_ok, node_msg = check_simple("node", "Node.js（Codex 需要）")
    git_ok, git_msg = check_simple("git", "Git")
    gh_ok, gh_msg = check_gh()
    py_ok, py_msg = check_simple("python", "Python 3.11+") if not getattr(sys, "frozen", False) else (True, "內建於 App")
    return [
        {"key": "claude", "name": "Claude Code", "ok": claude_ok, "msg": claude_msg, "critical": True},
        {"key": "codex", "name": "OpenAI Codex", "ok": codex_ok, "msg": codex_msg, "critical": True},
        {"key": "node", "name": "Node.js", "ok": node_ok, "msg": node_msg, "critical": False},
        {"key": "git", "name": "Git", "ok": git_ok, "msg": git_msg, "critical": False},
        {"key": "gh", "name": "GitHub CLI", "ok": gh_ok, "msg": gh_msg, "critical": False},
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


# shell / cmd 語法字元 → 安全替代。需求是自由文字（多半是中文），所以在 prose 裡
# 有意義的字元（`% ! & $ ; < >`）改成**全形等價字**保住語意，純語法字元才刪掉。
# 為什麼不用「正確地引用」解決：cmd 的 `%VAR%` 展開發生在解析之後，值裡的 `&`
# 仍會被當成命令分隔符——在 cmd 上不存在能擋住一切的引用方式。
_META_MAP = {
    '"': "", "'": "", "`": "", "|": "", "^": "", "\\": "",   # 純語法，刪除
    "$": "＄", "&": "＆", ";": "；", "<": "＜", ">": "＞",     # prose 常用，轉全形
    "%": "％", "!": "！",
    "\n": " ", "\r": " ", "\t": " ",                          # 命令列不能有換行
}
_META_TABLE = str.maketrans(_META_MAP)


def _safe_prompt(text: str) -> str:
    """把使用者需求清成「可安全放進命令列的純文字」。

    launcher 會把需求交給終端機執行 `claude "<需求>"`，中間必經一層 shell
    （Windows 的 `cmd /k`、Linux 的 `bash -lc`）。不清理的話，含
    ``$(...)`` / `` `...` `` / ``&`` 的需求會被那層 shell 當成指令執行——
    使用者自己輸入自己的需求，風險低，但這是不必要的執行路徑。

    中文與全形標點（`（）「」`）完全不受影響；`100%` 會變成 `100％`、
    `A & B` 變成 `A ＆ B`，語意保留但對 shell 失去意義。
    """
    return " ".join((text or "").translate(_META_TABLE).split())


def launch_claude(requirement: str, autopilot: bool = False) -> bool:
    """開新終端機在 app 目錄跑互動式 claude，需求當初始 prompt。

    autopilot=True 時走非停模式：prompt 前綴 ``/autopilot on``（與 extension 的
    「非停」QuickPick 一致），連回合都不停一路跑到交付（commit/push 仍會問）。

    所有 `Popen` 一律傳 **argv list 且不帶 shell=True**，需求先過 `_safe_prompt`。
    POSIX 分支再多一層保險：需求以 **bash 位置參數**（``"$2"``）傳入，bash 從頭到尾
    不會把它當程式碼解析。
    """
    if not _which("claude"):
        messagebox.showerror("CodexAutoAI", "找不到 claude，請先按「設定/修復」安裝並登入。")
        return False
    req = _safe_prompt(requirement)
    prompt = f"/autopilot on {req}".strip() if autopilot else req
    try:
        if IS_WIN:
            # claude 在 Windows 是 .cmd 蓋子，必須由 cmd 執行；argv list 交給
            # Popen 自行加引號，不自己拼字串。
            tail = ["cmd", "/k", "claude"] + ([prompt] if prompt else [])
            wt = _which("wt")
            if wt:             # 優先 Windows Terminal
                subprocess.Popen([wt, "-d", str(APP_DIR)] + tail)
            else:
                # `start` 是 cmd 內建，需要 cmd 承載；第二個引數是視窗標題。
                subprocess.Popen(["cmd", "/c", "start", "CodexAutoAI"] + tail,
                                cwd=str(APP_DIR))
        else:
            # 需求走位置參數 $2，永遠不被 bash 解析成程式碼。
            script = 'cd "$1" && exec claude ${2:+"$2"}'
            argv = ["bash", "-lc", script, "bash", str(APP_DIR), prompt]
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                path = _which(term)
                if path:
                    subprocess.Popen([path, "-e"] + argv)
                    break
            else:
                subprocess.Popen(argv)
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
        root.geometry("560x760")
        root.minsize(520, 720)
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
        self._update_info: dict | None = None
        self._build()
        self.refresh()
        self.poll_progress()
        self.start_update_check()

    def _build(self) -> None:
        tk.Label(self.root, text="CodexAutoAI", font=self.h1, fg=GOLD, bg=BG).pack(pady=(22, 2))
        tk.Label(self.root, text="一句話描述需求，自動跑完需求→架構→寫碼→測試→交付",
                 font=self.h2, fg=MUTED, bg=BG).pack(pady=(0, 16))

        # 更新橫幅（預設隱藏，背景檢查到新版才 pack 進來）
        self.update_banner = tk.Frame(self.root, bg="#2a2410")
        self.update_text = tk.Label(self.update_banner, text="", font=self.h2,
                                    fg=GOLD, bg="#2a2410", anchor="w", justify="left")
        self.update_text.pack(side="left", fill="x", expand=True, padx=(12, 8), pady=8)
        self.update_btn = tk.Button(self.update_banner, text="⬇ 立即更新",
                                    command=self.on_update_apply, font=self.h2,
                                    bg=GOLD, fg=BG, relief="flat", padx=10, pady=2)
        self.update_btn.pack(side="left", padx=(0, 6), pady=8)
        tk.Button(self.update_banner, text="✕", command=self.dismiss_update, font=self.h2,
                  bg="#2a2410", fg=MUTED, relief="flat", padx=6).pack(side="left", padx=(0, 10))

        # 環境檢查卡
        self.card = card = tk.Frame(self.root, bg=CARD)
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

        # 執行模式：勾選＝非停（autopilot），連回合都不停一路跑到交付（commit/push 仍會問）。
        self.autopilot_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            self.root,
            text="非停模式（autopilot）：連回合都不停，一路跑到交付（commit/push 仍會問）",
            variable=self.autopilot_var, font=self.mono, fg=MUTED, bg=BG,
            activebackground=BG, activeforeground=CHAMPAGNE, selectcolor=CARD,
            anchor="w",
        ).pack(fill="x", padx=24, pady=(8, 0))

        self.launch_btn = tk.Button(self.root, text="🚀 啟動 CodexAutoAI", command=self.on_launch,
                                    font=self.h1, bg=GOLD, fg=BG, relief="flat", pady=8)
        self.launch_btn.pack(fill="x", padx=22, pady=18)

        # 進度卡：讀 log/events.jsonl（共用 tools/events_model.py）。
        # 在這之前，App 按下啟動後就只是「開了一個終端機」——pipeline 跑到哪、
        # 卡在哪、花了多少，全都只能去終端機看。這張卡把它拉回 App 內。
        self.prog_card = tk.Frame(self.root, bg=CARD)
        self.prog_card.pack(fill="x", padx=22, pady=(0, 10))
        head = tk.Frame(self.prog_card, bg=CARD)
        head.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(head, text="Pipeline 進度", font=self.h2, fg=CHAMPAGNE,
                 bg=CARD).pack(side="left")
        self.abort_btn = tk.Button(head, text="■ 中止", command=self.on_abort,
                                   font=self.mono, bg="#21262d", fg=MUTED,
                                   relief="flat", padx=10)
        self.abort_btn.pack(side="right")
        self.prog_bar = tk.Label(self.prog_card, text="", font=self.mono, fg=GOLD,
                                 bg=CARD, anchor="w")
        self.prog_bar.pack(fill="x", padx=14)
        self.prog_detail = tk.Label(self.prog_card, text="", font=self.mono, fg=MUTED,
                                    bg=CARD, anchor="w", justify="left")
        self.prog_detail.pack(fill="x", padx=14, pady=(2, 12))

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

    # ── 進度輪詢 ────────────────────────────────────────────────────────────
    def _events_log(self) -> Path:
        return APP_DIR / "log" / "events.jsonl"

    def poll_progress(self) -> None:
        """每 2 秒讀一次 events.jsonl 更新進度卡（唯讀，不干擾 pipeline）。"""
        try:
            model = load_progress_model(self._events_log())
        except Exception:
            model = None

        if not model or not model.get("log_exists"):
            self.prog_bar.config(text="尚未開始", fg=MUTED)
            self.prog_detail.config(text="按上面的按鈕啟動，這裡會顯示 Phase 進度")
            self.abort_btn.config(state="disabled", fg=MUTED)
        else:
            bar = "".join("▓" if i <= model["marker"] else "░"
                          for i in range(model["total"] + 1))
            colour = {"escalated": RED, "done": GREEN}.get(model["state"], GOLD)
            self.prog_bar.config(
                text=f"Phase {model['marker']}/{model['total']} {bar} {model['current_name']}",
                fg=colour)
            bits = [f"已完成：{model['completed'] or '無'}"]
            if model["iteration"]:
                bits.append(f"迭代 {model['iteration']}")
            if model["cost_usd"]:
                bits.append(f"${model['cost_usd']:.4f}")
            if model["errors"]:
                bits.append(f"錯誤：{model['errors'][-1]['reason']}")
            self.prog_detail.config(text="　".join(bits))
            running = model["state"] == "running"
            self.abort_btn.config(state="normal" if running else "disabled",
                                  fg=CHAMPAGNE if running else MUTED)

        self.root.after(2000, self.poll_progress)

    def on_abort(self) -> None:
        """放下中止旗標。只在**回合邊界**生效，殺不掉正在跑的 codex 子行程。"""
        if not messagebox.askyesno(
            "CodexAutoAI",
            "要中止目前的 pipeline 嗎？\n\n"
            "會在下一個回合邊界停止（無法立即中斷正在執行的 Codex）。",
        ):
            return
        try:
            flag = APP_DIR / "log" / "abort.flag"
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("", encoding="utf-8")
            self.status.config(text="已送出中止，將於下一個回合邊界停止", fg=GOLD)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("CodexAutoAI", f"寫入中止旗標失敗：{exc}")

    def on_setup(self) -> None:
        # 環境 pre-check（與 extension skipSetupWhenReady 一致）：關鍵項都就緒就不開終端機。
        checks = gather_checks()
        self.refresh()
        missing = [c for c in checks if c["critical"] and not c["ok"]]
        if not missing:
            self.status.config(text="✓ Claude / Codex 已安裝並登入，無需重跑設定", fg=GREEN)
            return
        run_setup()
        self.status.config(text="設定視窗已開啟，完成後請按「↻ 重新檢查」", fg=GOLD)

    def on_launch(self) -> None:
        req = self.req.get("1.0", "end").strip()
        if launch_claude(req, autopilot=self.autopilot_var.get()):
            mode = "非停（autopilot）" if self.autopilot_var.get() else "一般"
            self.status.config(text=f"已開啟終端機（{mode}），CodexAutoAI 在新視窗執行中…", fg=GREEN)

    # ── 版本檢查 / 更新 ──────────────────────────────────────────────────────
    def start_update_check(self) -> None:
        """背景 thread 查 GitHub Release，避免卡住 UI；查到新版才顯示橫幅。"""
        if updater is None:
            return

        def worker() -> None:
            try:
                info = updater.check_update()
            except Exception:  # noqa: BLE001 — 檢查失敗就靜默
                return
            # 回主執行緒更新 UI（tkinter 非 thread-safe）
            self.root.after(0, lambda: self._on_update_result(info))

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_result(self, info: dict) -> None:
        if not info or not info.get("update_available"):
            return
        self._update_info = info
        latest, current = info.get("latest"), info.get("current")
        self.update_text.config(
            text=f"🎉 有新版本 v{latest}（目前 v{current}）")
        # 安裝版才提供一鍵更新；dev/無資產時退化成「查看 Release」
        if info.get("installed") and info.get("asset_name"):
            self.update_btn.config(text="⬇ 立即更新", command=self.on_update_apply)
        else:
            self.update_btn.config(text="↗ 查看 Release", command=self.open_release)
        self.update_banner.pack(fill="x", padx=22, pady=(0, 10), before=self.card)

    def open_release(self) -> None:
        info = self._update_info or {}
        url = info.get("html_url") or f"https://github.com/{(updater.repo() if updater else '')}/releases"
        webbrowser.open(url)

    def dismiss_update(self) -> None:
        self.update_banner.pack_forget()

    def on_update_apply(self) -> None:
        if updater is None:
            return
        self.update_btn.config(state="disabled", text="⏳ 下載中…")
        self.status.config(text="正在下載更新…", fg=GOLD)

        def worker() -> None:
            try:
                res = updater.apply_update()
            except Exception as exc:  # noqa: BLE001
                res = {"status": "error", "error": str(exc)}
            self.root.after(0, lambda: self._on_update_applied(res))

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_applied(self, res: dict) -> None:
        st = res.get("status")
        if st == "updating":
            self.update_text.config(
                text=f"⬇ 安裝程式已開啟：v{res.get('from')} → v{res.get('to')}，"
                     "請依精靈完成更新後重新開啟。")
            self.update_btn.pack_forget()
            self.status.config(text="安裝程式已啟動，完成後請重開 App", fg=GREEN)
        elif st == "refused":
            self.update_btn.config(state="normal", text="↗ 查看 Release",
                                  command=self.open_release)
            self.status.config(text=res.get("error", "非安裝版，請用 git pull"), fg=GOLD)
        else:
            self.update_btn.config(state="normal", text="⬇ 立即更新")
            self.status.config(text="更新失敗：" + str(res.get("error")), fg=RED)


def _overlay_token() -> str:
    return f"desktop:{os.getpid()}"


def main() -> int:
    # 啟動：套用全域 Claude/Codex 設定；關閉（正常關 / atexit）一定還原。
    released = {"done": False}

    def release_overlay() -> None:
        if released["done"] or global_overlay is None:
            return
        released["done"] = True
        try:
            global_overlay.release(_overlay_token())
        except Exception:  # noqa: BLE001 — 還原失敗不該擋住關閉
            pass

    if global_overlay is not None:
        try:
            global_overlay.acquire(_overlay_token())
        except Exception:  # noqa: BLE001 — 套用失敗不該擋住啟動
            pass
        import atexit
        atexit.register(release_overlay)

    root = tk.Tk()

    def on_close() -> None:
        release_overlay()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    LauncherUI(root)
    try:
        root.mainloop()
    finally:
        release_overlay()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
