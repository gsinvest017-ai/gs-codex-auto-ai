#!/usr/bin/env python3
"""
launcher.py — CodexAutoAI 桌面啟動器（tkinter，純標準庫）。

給完全不懂指令的人：點桌面圖示 → 看環境是否就緒 → 一鍵「設定/修復」把能自動的跑掉 →
勾選是否要「非停模式（autopilot）」→ 在輸入框打一句需求按「啟動」→ 預設在**內嵌終端機**
（App 內分頁管理，見 conpty/sessions/termserver）開一個 session 跑互動式
claude，完整七階段開始。啟動後主視窗顯示 Pipeline 進度卡（讀 `log/events.jsonl`），可按
「■ 中止」於下一個回合邊界停止 pipeline（寫 `log/abort.flag`）。

App 取代不了 claude / codex / node 這些 CLI 本身（登入要開瀏覽器），所以這裡只做：
偵測缺什麼、把能自動的自動化（呼叫 repo 的 setup.cmd）、其餘明確引導，全綠才啟用「啟動」。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

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

try:
    import winembed  # 把終端機頁面嵌進 App 視窗右欄（Windows only，sibling module）
except Exception:  # noqa: BLE001 — 缺模組就退回獨立視窗，不擋啟動
    winembed = None

# ── GS 暗金主題 ──────────────────────────────────────────────────────────────
BG = "#0f1115"          # warm-black
CARD = "#171a21"
GOLD = "#d4af37"
CHAMPAGNE = "#e7ddc7"
GREEN = "#3fb950"
RED = "#f85149"
MUTED = "#8b949e"
GOLD_HOVER = "#e6c250"   # 主按鈕 hover

# 版面：左邊控制欄的固定寬度、右邊終端機欄展開時要多給的寬度。
LEFT_W = 560
TERM_W = 900

# 內嵌診斷檔最多保留幾行（見 _note_embed）。
_EMBED_LOG_LINES = 200


def app_dir() -> Path:
    """框架檔（.claude/ CLAUDE.md tools/ setup.*）所在目錄。
    凍結（PyInstaller）時 exe 與框架檔同目錄；未凍結時取 repo 根（desktop/ 的 parent）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_DIR = app_dir()
IS_WIN = os.name == "nt"

# 設定檔：記住使用者選的專案資料夾。放在 App 目錄外，重裝 / 升級都不會被蓋掉。
CONFIG_PATH = Path.home() / ".codexautoai" / "desktop.json"


def default_project_dir() -> Path:
    r"""預設的專案資料夾。

    **刻意不是安裝目錄**：以前所有 session 都跑在 `%LOCALAPPDATA%\CodexAutoAI`，
    於是使用者的產出被寫進 App 的安裝目錄，而且每個任務共用同一份 `log/`——
    上一輪的 Phase 進度會被下一輪看到（進度卡顯示「Phase 7/7 已完成 [0..7]」
    就是這樣來的），解除安裝還會把人家的專案一起帶走。
    """
    return Path.home() / "CodexAutoAI"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 設定壞掉就當預設，不擋啟動
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def project_dir() -> Path:
    p = load_config().get("project_dir")
    return Path(p) if p else default_project_dir()


def set_project_dir(path: Path) -> None:
    cfg = load_config()
    cfg["project_dir"] = str(path)
    save_config(cfg)


# App 隨附、pipeline 執行必需的框架檔。專案資料夾裡沒有這些，claude 就讀不到
# dispatcher 指示（CLAUDE.md）、也載不到那三個 hook（.claude/settings.json）——
# 等於七階段流程與 Codex-first 守門員全部不存在。
FRAMEWORK_ENTRIES = (".claude", "CLAUDE.md", "AGENTS.md", "tools", "usage_gate.toml")
STAMP_NAME = ".codexautoai-framework"


def app_version() -> str:
    try:
        return (APP_DIR / "desktop" / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def bootstrap_project(root: Path) -> list[str]:
    """把框架檔補進專案資料夾，回傳這次複製了哪些。

    **這是把 cwd 從安裝目錄搬走之後的必要條件。** 以前 session 直接跑在 APP_DIR，
    框架檔本來就在腳下；換成使用者自己的資料夾之後，那裡是空的——沒有 CLAUDE.md
    就沒有 dispatcher、沒有 `.claude/settings.json` 就一個 hook 都不會載入，
    七階段與 Codex-first 守門員全部形同不存在。

    版本戳記不同就整批覆蓋：這些檔案是 App 隨附的框架，跟 App 版本必須一致；
    留著舊的比沒有更難查（指示與實際行為對不上）。
    """
    copied: list[str] = []
    try:
        root.mkdir(parents=True, exist_ok=True)
        stamp = root / STAMP_NAME
        cur = app_version()
        # **第一次接管一個資料夾時絕不覆蓋任何既有檔案。** 使用者很自然會直接指向
        # 自己現有的專案，而那裡可能已經有他自己的 `CLAUDE.md` / `.claude/` /
        # `tools/`——同名就蓋掉是無聲的資料破壞。只有「這個資料夾是我們裝的」
        # （戳記存在）而且版本變了，才更新我們自己放的那份。
        # 戳記記下「版本」與「哪些是我們放的」。只靠版本不夠：使用者自己的
        # `CLAUDE.md` 在第一次接管時被正確保留了，但沒有記錄的話，下次 App 升級
        # （版本變了）就會把它一起蓋掉——保護只擋得住第一次。
        try:
            meta = json.loads(stamp.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                meta = {}
        except (OSError, ValueError):
            meta = {}          # 舊格式（純版本字串）或不存在 → 當成沒裝過，保守不覆蓋
        installed = set(meta.get("installed") or [])
        upgraded = bool(meta.get("version")) and meta.get("version") != cur
        for name in FRAMEWORK_ENTRIES:
            src = APP_DIR / name
            dst = root / name
            if not src.exists():
                continue
            # 只有「這份是我們放的」而且「App 版本變了」才更新；其餘一律不碰。
            if dst.exists() and not (upgraded and name in installed):
                continue
            try:
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
                copied.append(name)
                installed.add(name)
            except Exception:  # noqa: BLE001 — 少一個檔不該擋住啟動
                pass
        stamp.write_text(json.dumps({"version": cur,
                                     "installed": sorted(installed)},
                                    ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return copied


_RESOLVED_ROOT: Optional[Path] = None


CLAUDE_STATE = Path.home() / ".claude.json"


def _claude_dir_is_ours(root: Path) -> bool:
    """那個資料夾的 `.claude/` 是我們 bootstrap 放進去的嗎。

    戳記裡記著我們實際複製過哪些項目（見 `bootstrap_project`）。沒有 `.claude/`
    也算安全——那時 claude 沒有任何預先核准的 hook 可以跑。
    """
    if not (root / ".claude" / "settings.json").exists():
        return True
    try:
        meta = json.loads((root / STAMP_NAME).read_text(encoding="utf-8"))
        return ".claude" in (meta.get("installed") or [])
    except (OSError, ValueError, AttributeError):
        return False


def trust_project_dir(root: Path) -> bool:
    """把專案資料夾標記成「已信任」，讓 claude 不再跳資料夾信任確認。

    Claude Code 把這個狀態記在 `~/.claude.json` 的
    `projects["<路徑>"].hasTrustDialogAccepted`（路徑用正斜線）。不設的話，每個
    新資料夾第一次開 session 都會停在「Yes, I trust this folder」等人按 Enter
    ——而內嵌面板現在還沒辦法接受鍵盤輸入，等於直接卡死。

    只標記**使用者自己在 App 裡選的**那個資料夾，不碰其他任何專案。回傳有沒有
    真的寫入（已經是信任狀態、或不該自動信任就回 False）。

    這是對共用檔案的 read-modify-write，沒有上鎖：同一時間別的 claude 行程也在寫
    的話，它那次的變更會被覆蓋掉（原子 replace 只保證檔案不會壞，不保證不掉更新）。
    接受這個風險是因為窗口極窄——只在 `prepare_project_dir()` 啟動時跑一次；跨平台
    檔案鎖的複雜度不值得為這個換。

    **界線：只自動信任「`.claude/` 是我們放的」資料夾。** `.claude/settings.json`
    裡的 hook 會執行任意 shell 指令，那正是 claude 要跳信任確認的原因。使用者
    把 App 指向一個**本來就有自己 `.claude/`** 的既有專案時（bootstrap 刻意不覆蓋
    它），那份設定的內容我們一無所知——替他跳過確認等於把同意權拿掉。這種情況
    保留原本的確認流程，由使用者自己看過再決定。
    """
    if not _claude_dir_is_ours(root):
        return False
    try:
        try:
            data = json.loads(CLAUDE_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            return False
        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            return False
        key = str(root).replace("\\", "/")
        entry = projects.get(key)
        if not isinstance(entry, dict):
            entry = {}
        if entry.get("hasTrustDialogAccepted") is True:
            return False
        entry["hasTrustDialogAccepted"] = True
        projects[key] = entry
        # 先寫暫存再換掉：這個檔有 170 KB 的使用者狀態，寫到一半掛掉會全毀。
        tmp = CLAUDE_STATE.with_suffix(".json.codexautoai-tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CLAUDE_STATE)
        return True
    except Exception:  # noqa: BLE001 — 信任設定失敗不該擋住啟動
        return False


def prepare_project_dir() -> Path:
    """取得專案資料夾並確保它可用（建目錄 + 補框架檔）。建不出來就退回安裝目錄。"""
    global _RESOLVED_ROOT
    root = project_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        root = APP_DIR
    else:
        bootstrap_project(root)
        trust_project_dir(root)
    _RESOLVED_ROOT = root
    return root


def active_project_dir() -> Path:
    """session 實際跑在哪。

    **不能直接用 `project_dir()`**：那是使用者設定的值，而 `prepare_project_dir()`
    在建不出目錄時會退回安裝目錄。兩邊不一致的話，session 跑在 A、心跳與進度卡
    寫/讀 B——守門員在 A 找不到標記就 fail-open，Claude 可以直接寫 App 自己的
    `src/`，而且完全沒有徵兆。
    """
    return _RESOLVED_ROOT or project_dir()


class PrimaryButton:
    """主要動作按鈕。

    tkinter 的 `tk.Button` 只吃**單一字型**，所以「大標＋小字說明」做不出來，
    而且它自己畫的邊框在暗色主題下很突兀。這裡改用 Frame + 兩個 Label 自己組，
    換得雙行排版、hover 回饋、以及對內距的完整控制。

    對外維持 `config(state=…)` / `cget("state")`，`refresh()` 與
    `_set_actions_enabled()` 沿用原本的寫法即可。
    """

    def __init__(self, parent, title: str, subtitle: str, command,
                 title_font, sub_font) -> None:
        self._command = command
        self._state = "normal"
        # takefocus + 鍵盤啟動：`tk.Button` 本來就能 Tab 過去再按 Space/Enter，
        # 自己用 Frame 組就得自己補回來，否則只剩滑鼠能按。
        self.frame = tk.Frame(parent, bg=GOLD, cursor="hand2",
                              highlightthickness=2, highlightbackground=GOLD,
                              highlightcolor="#ffffff", takefocus=True)
        inner = tk.Frame(self.frame, bg=GOLD)
        inner.pack(fill="x", padx=18, pady=(12, 13))
        self._title = tk.Label(inner, text=title, font=title_font, bg=GOLD, fg="#1a1206")
        self._title.pack()
        self._sub = tk.Label(inner, text=subtitle, font=sub_font, bg=GOLD, fg="#5c4a1e")
        self._sub.pack(pady=(2, 0))
        for w in (self.frame, inner, self._title, self._sub):
            w.bind("<Button-1>", self._focus_then_click)
            w.bind("<Enter>", lambda _e: self._paint(GOLD_HOVER))
            w.bind("<Leave>", lambda _e: self._paint(GOLD))
        for seq in ("<Return>", "<KP_Enter>", "<space>"):
            self.frame.bind(seq, self._click)

    # -- 外觀 ---------------------------------------------------------------
    def _paint(self, bg: str) -> None:
        if self._state != "normal":
            return
        for w in (self.frame, self._title.master, self._title, self._sub):
            w.config(bg=bg)

    def _focus_then_click(self, event=None) -> None:
        if self._state == "normal":
            self.frame.focus_set()
        self._click(event)

    def _click(self, _event=None) -> None:
        if self._state == "normal" and self._command:
            self._command()

    # -- 相容 tk.Button 的介面 ------------------------------------------------
    def config(self, **kw):
        if "state" in kw:
            self._state = kw["state"]
            on = self._state == "normal"
            bg = GOLD if on else "#2a2d35"
            self.frame.config(cursor="hand2" if on else "arrow")
            for w in (self.frame, self._title.master):
                w.config(bg=bg)
            self._title.config(bg=bg, fg="#1a1206" if on else MUTED)
            self._sub.config(bg=bg, fg="#5c4a1e" if on else "#4a4d55")

    configure = config

    def cget(self, key):
        return self._state if key == "state" else None

    def pack(self, **kw):
        self.frame.pack(**kw)
        return self


class EmbeddedTerminal:
    """內嵌終端機：在 App 內用分頁管理 Claude / Codex session。

    取代原本「每開一個 session 就 Popen 一個外部原生終端機視窗」的做法——那會讓
    session 散在桌面各處、關掉 App 也收不乾淨、App 完全看不到裡面在幹嘛。

    服務是**延遲啟動**的（第一次用到才起），沒用到就不佔 port、不開執行緒。
    外殼優先用 pywebview（原生視窗），沒裝就退回預設瀏覽器——這樣沒裝任何額外
    套件的公開使用者照樣能用，裝了才升級體驗，`desktop/` 的純標準庫不變式不破。
    """

    def __init__(self) -> None:
        self._srv = None
        self._mgr = None
        self._lock = threading.Lock()
        # 關閉時要一併收掉的東西（例如嵌在右欄裡的瀏覽器外殼）。App 的關閉路徑
        # 只認得 TERMINAL.shutdown()，UI 層自己註冊進來比讓 main() 多認一個物件簡單。
        self._closers: list = []

    def add_closer(self, fn) -> None:
        with self._lock:
            self._closers.append(fn)

    def available(self) -> tuple[bool, str]:
        """這台機器能不能用內嵌終端機。"""
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import conpty  # noqa: PLC0415
            if not conpty.pty_available():
                return False, "這台 Windows 太舊（內嵌終端機需要 Windows 10 1809 以上）"
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, f"載入 PTY 後端失敗：{exc}"

    def _ensure(self):
        with self._lock:
            if self._srv is not None:
                return self._srv
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import sessions as sessions_mod   # noqa: PLC0415
            import termserver                 # noqa: PLC0415
            root = prepare_project_dir()
            self._mgr = sessions_mod.SessionManager(default_cwd=str(root))
            self._srv = termserver.TerminalServer(self._mgr).start()
            return self._srv

    def open(self, *, kind: str | None = None, prompt: str = "",
             shell=None) -> bool:
        """開啟（或聚焦）終端機；可順便先建一個 session。

        回傳「呼叫端要的事情有沒有成功」——建 session 失敗時回 False。以前失敗只
        跳一個 messagebox 就正常返回，呼叫端無從得知，於是照樣回報「已開啟 Claude
        分頁」、守門員也一直上著膛，實際上一個 session 都沒有。

        **刻意不回傳 URL**：這個方法的用途常常是「給我一個 URL 丟給外面的東西開」，
        而唯一該外流的是 handoff 券。以前回 `srv.url`（帶真 token）沒被利用只是因為
        目前沒人接它的回傳值——那是個等著被踩的坑。要 URL 的人自己去拿
        `srv.handoff_url`。

        `shell(open_url) -> bool` 是「把頁面顯示在 App 視窗內」的實作（由 UI 層提供）。
        回 False 或沒提供，就退回 `_show()`（pywebview 原生視窗 → 瀏覽器分頁）。

        傳給它的是**取得 URL 的函式**而不是 URL 本身：每叫一次就發一張新的
        handoff 券，而 shell 有可能根本不需要導頁（面板已經嵌著、只是重新展開）。
        先算好就等於白發一張券。
        """
        srv = self._ensure()
        if kind:
            try:
                self._mgr.create(kind=kind, prompt=prompt)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("CodexAutoAI", f"開啟 {kind} session 失敗：{exc}")
                return False
        # 交給另一個行程（瀏覽器外殼）開的 URL 一律用 handoff nonce——命令列在
        # Windows 上同機任何帳號都讀得到，token 放上去等於公開。見 termserver 說明。
        if shell is not None:
            try:
                if shell(lambda: srv.handoff_url):
                    return True
            except Exception:  # noqa: BLE001 — 內嵌只是體驗升級，壞了就走舊路
                pass
        self._show(srv.handoff_url)
        return True

    def _show(self, url: str) -> None:
        """退路：優先開原生視窗（pywebview），沒有就用預設瀏覽器。"""
        try:
            import webview  # noqa: PLC0415  # pywebview，選用
        except Exception:  # noqa: BLE001
            webbrowser.open(url)
            return
        try:
            webview.create_window("CodexAutoAI 終端機", url, width=1100, height=720)
            threading.Thread(target=webview.start, daemon=True).start()
        except Exception:  # noqa: BLE001
            webbrowser.open(url)

    def has_live_sessions(self) -> bool:
        with self._lock:
            mgr = self._mgr
        return bool(mgr and mgr.alive_sessions())

    def reset_workdir(self) -> None:
        """換了專案資料夾之後，讓下一次 open() 重建服務。

        `default_cwd` 是 `SessionManager` 建構時就決定的，服務又是延遲啟動且只建
        一次——不重置的話，換資料夾在 UI 上看起來成功了，session 卻還是開在舊路徑。
        """
        with self._lock:
            srv, self._srv, self._mgr = self._srv, None, None
        if srv is not None:
            try:
                srv.stop()
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self) -> None:
        """App 結束時收乾淨——不收的話 pty 子行程（claude/node）會變孤兒。"""
        with self._lock:
            srv, self._srv = self._srv, None
            closers, self._closers = self._closers, []
        for fn in closers:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        if srv is not None:
            try:
                srv.stop()
            except Exception:  # noqa: BLE001
                pass


def touch_app_run(root: Path, prompt: str = "") -> None:
    """寫 / 更新「App 正在跑一個任務」的標記。

    這是 `tools/enforce_build_codex.py` 的**機械式武裝來源**：原本守門員只認
    `log/state.json`，而那是靠 Claude 自己去跑 `run_phase.py begin` 寫的——它不寫，
    守門員就 fail-open 放行，等於「Claude 不直接寫程式碼」最後還是靠 Claude 自律。
    改由 App 寫這個檔（啟動任務時建立、進度輪詢時更新心跳），跟 LLM 有沒有照做無關。

    寫失敗只是少一層保護，不該擋住任務啟動，所以全程吞例外。
    """
    try:
        log = root / "log"
        log.mkdir(parents=True, exist_ok=True)
        f = log / "app-run.json"
        try:
            cur = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cur = {}
        if prompt:
            cur["prompt"] = prompt[:200]
            cur["started_at"] = time.time()
        cur["updated_at"] = time.time()
        cur["pid"] = os.getpid()
        f.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


# tk 的 keysym → 要送進 pty 的位元組。終端機吃的是 ANSI 序列，不是按鍵名。
_KEYSYM_BYTES = {
    "Return": "\r", "KP_Enter": "\r", "Tab": "\t", "BackSpace": "\x7f",
    "Escape": "\x1b", "Delete": "\x1b[3~", "Insert": "\x1b[2~",
    "Up": "\x1b[A", "Down": "\x1b[B", "Right": "\x1b[C", "Left": "\x1b[D",
    "Home": "\x1b[H", "End": "\x1b[F", "Prior": "\x1b[5~", "Next": "\x1b[6~",
}


def keystroke_to_bytes(keysym: str, char: str, ctrl: bool) -> str:
    """把一次 tk 按鍵事件翻成終端機看得懂的輸入。回空字串代表不要送。

    **為什麼需要這層**：內嵌的瀏覽器視窗被 SetParent 進 App 之後，鍵盤事件進不到
    Chromium 的 renderer（實測頁面自己回報 `document.hasFocus()=true`、
    `activeElement` 也是 xterm 的 textarea，但打字沒有任何反應——按鍵在到達網頁
    之前就被丟掉了）。所以改由 tk 收鍵盤，直接寫進 pty；瀏覽器只負責顯示。
    """
    if keysym in _KEYSYM_BYTES:
        return _KEYSYM_BYTES[keysym]
    if ctrl:
        # Ctrl+A..Z → 0x01..0x1a（Ctrl+C 中斷、Ctrl+D EOF 都靠這個）
        if len(keysym) == 1 and keysym.isalpha():
            return chr(ord(keysym.upper()) - 64)
        return ""
    if char and char.isprintable():
        return char
    return ""


def _note_embed(msg: str) -> None:
    """把視窗內嵌失敗的原因寫到一個固定位置。

    狀態列那行是**瞬間**的：使用者回報「跳出兩個視窗」時往往已經看不到原因，
    只能靠猜。寫進 temp 目錄（一定寫得進去，不像 Program Files 底下的 log/），
    之後要診斷就是一句「把這個檔貼給我」。
    """
    try:
        from datetime import datetime
        p = Path(tempfile.gettempdir()) / "codexautoai-embed.log"
        line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}\n"
        # 只留最後 N 行。診斷檔沒有輪替機制的話會隨 App 的壽命無限長大，
        # 而要診斷的本來就只有最近幾次。
        old = p.read_text(encoding="utf-8").splitlines(keepends=True) if p.exists() else []
        # max(..., 0)：上限若是 1，`-(1-1)` 會變成 `-0 == 0`，`old[0:]` 整份留下來
        # ——截斷會安靜地失效。
        keep = max(_EMBED_LOG_LINES - 1, 0)
        p.write_text(("".join(old[-keep:]) if keep else "") + line, encoding="utf-8")
    except Exception:  # noqa: BLE001 — 診斷紀錄不該成為新的故障點
        pass


TERMINAL = EmbeddedTerminal()


def load_progress_model(log_path: Path) -> dict | None:
    """讀 `log/events.jsonl` 的進度模型（共用 `tools/events_model.py`）。

    刻意**每次呼叫時才解析模組**而非 top-level import：`tools/` 不是套件、
    且凍結（PyInstaller）後的佈局與 repo 佈局不同，寫死 import 會在其中一種
    情境下壞掉。找不到模組就回 None，進度卡降級顯示、絕不擋住啟動器。

    VS Code 的控制台（`vscode-extension/dashboard.js`）讀同一份模型，
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


MIN_PY = (3, 11)


def check_python() -> tuple[bool, str]:
    """PATH 上有沒有**外部** Python ≥ 3.11。

    凍結版以前直接回報「內建於 App」就過關——那是錯的，而且錯得很安靜。exe 自己
    嵌的 runtime 只給 App 用；`.claude/settings.json` 的三個 hook 全都是
    `python "$CLAUDE_PROJECT_DIR/tools/…"`，要另外開行程，吃的是 PATH。

    沒有它的後果不是「某個功能不能用」，而是**整套保證失效而且沒有徵兆**：
    enforce_build_codex（Codex-first 硬分工的守門員）、dispatch_hook（進度）、
    autopilot 的 arm/cont 全部靜默失敗，pipeline 照跑，但「Claude 不直接寫程式碼」
    這條核心不變式就沒人守了。所以這項是 **critical**。
    """
    too_old = ""
    for name in ("python", "python3"):
        path = _which(name)
        if not path:
            continue
        rc, out = _run(f'"{path}" -c "import sys;print(*sys.version_info[:2])"')
        if rc != 0:
            continue
        # `_run` 會把 stdout 與 stderr 串在一起，pyenv / sitecustomize 之類的 banner
        # 會混進來——只看最後一行的話一個 banner 就讓解析失敗，而這一關現在是
        # critical，失敗會靜默降級成「版本判讀失敗但放行」。改成掃每一行找得出來的。
        ver = None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and all(x.isdigit() for x in parts):
                ver = (int(parts[0]), int(parts[1]))
        if ver is None:
            return True, f"已安裝（{path}，版本判讀失敗）"
        major, minor = ver
        if (major, minor) < MIN_PY:
            # **記下來繼續找下一個候選**，不能就此放棄：Linux 上 `python` 常常是
            # 舊的、真正能用的是 `python3`。這項現在是 critical，提早 return 會
            # 讓一台其實裝了 3.12 的機器按不下「啟動」。
            too_old = too_old or (f"版本太舊 {major}.{minor}（需要 "
                                  f"{MIN_PY[0]}.{MIN_PY[1]}+）— hooks 會失效")
            continue
        return True, f"已安裝（{path}，{major}.{minor}）"
    return False, too_old or "未安裝 — hooks 需要它，缺了 Codex-first 守門員會失效"


# 檢查項的**靜態**清單：(key, 顯示名, critical, 檢查函式)。
# UI 只靠前三欄就能把列畫出來——`_build()` 以前是呼叫 `gather_checks()` 來列舉，
# 等於為了拿名字去跑一輪 `codex login status` / `gh auth status` / 探 python，
# 結果 ok/msg 立刻丟掉，既阻塞又白做。兩邊共用同一份清單也不會再對不起來。
# critical 的 python：hooks 全靠它，缺了整套 Codex-first 保證會靜默失效。
CHECKS = (
    ("claude", "Claude Code", True),
    ("codex", "OpenAI Codex", True),
    ("node", "Node.js", False),
    ("git", "Git", False),
    ("gh", "GitHub CLI", False),
    ("python", "Python", True),
)


def check_rows() -> list[tuple[str, str, bool]]:
    """畫 UI 用的列定義。**不跑任何子行程。**"""
    return list(CHECKS)


def _run_check(key: str) -> tuple[bool, str]:
    """跑單一項檢查。

    刻意在**呼叫當下**才查模組層級的函式名（而不是把函式物件存進 `CHECKS`）：
    存物件的話 monkeypatch 換掉模組屬性就不會生效，測試會替換不了。
    """
    if key == "claude":
        return check_claude()
    if key == "codex":
        return check_codex()
    if key == "node":
        return check_simple("node", "Node.js（Codex 需要）")
    if key == "git":
        return check_simple("git", "Git")
    if key == "gh":
        return check_gh()
    if key == "python":
        return check_python()
    return False, "未知的檢查項"


def gather_checks() -> list[dict]:
    """真的去檢查（會叫子行程，慢）。只在背景執行緒呼叫。"""
    out = []
    for key, name, critical in CHECKS:
        try:
            ok, msg = _run_check(key)
        except Exception as exc:  # noqa: BLE001 — 一項壞掉不該讓整排檢查消失
            ok, msg = False, f"檢查失敗：{exc}"
        out.append({"key": key, "name": name, "ok": ok, "msg": msg, "critical": critical})
    return out


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


def _spec_forge_candidates() -> list[tuple[str, dict]]:
    """依序嘗試的 (命令模板, 額外 env) 候選：SPECFORGE_CMD > ~/gs-spec-forge venv > PATH >
    內建快照（python -m，stdlib-first 核心）。前者失敗自動落到下一個——venv 斷根、沒裝
    gs-spec-forge、沒 gh / 沒 private repo 權限的使用者都能靠快照開箱即用。"""
    cands: list[tuple[str, dict]] = []
    env_cmd = os.environ.get("SPECFORGE_CMD")
    if env_cmd and (_which(env_cmd) or Path(env_cmd).exists()):
        cands.append((f'"{env_cmd}"', {}))
    venv = Path.home() / "gs-spec-forge" / ".venv" / (
        "Scripts/spec-forge.exe" if IS_WIN else "bin/spec-forge")
    if venv.exists():
        cands.append((f'"{venv}"', {}))
    if _which("spec-forge"):
        cands.append(("spec-forge", {}))
    # 內建快照：凍結版放 exe 旁、開發版在 vscode-extension/（build-vsix 產物）
    for snap in (APP_DIR / "spec_forge_snapshot",
                 APP_DIR / "vscode-extension" / "spec_forge_snapshot"):
        if (snap / "gs_spec_forge" / "cli.py").exists():
            env = {"PYTHONPATH": str(snap) + os.pathsep + os.environ.get("PYTHONPATH", ""),
                   "PYTHONIOENCODING": "utf-8"}
            cands.append(("python -m gs_spec_forge.cli", env))
            break
    return cands


def seed_from_spec(intent: str) -> Optional[str]:
    """用 gs-spec-forge 把一句意圖變成規格檔，回傳「要交給 claude 的需求字串」。

    **只負責產 spec，不負責啟動。** 以前它直接呼叫 `launch_claude()`，於是這條路
    永遠開在視窗外的原生終端機——內嵌面板那條路只有另一顆按鈕接得到。怎麼啟動
    （內嵌優先、外部終端機為退路）由 UI 決定，兩顆按鈕才會有一致的行為。

    候選鏈見 _spec_forge_candidates()；SPEC_VAULT 指定 vault（預設 ~/gs-vault，
    否則 App 目錄下 vault/）。失敗回 None（錯誤訊息已經跳過了）。
    """
    cands = _spec_forge_candidates()
    if not cands:
        messagebox.showerror(
            "CodexAutoAI",
            "找不到 spec-forge 也沒有內建快照。請先安裝 gs-spec-forge（跑其 install-spec-forge.ps1），"
            "或設環境變數 SPECFORGE_CMD 指到 spec-forge 執行檔。")
        return None
    vault = os.environ.get("SPEC_VAULT")
    if not vault:
        home_vault = Path.home() / "gs-vault"
        vault = str(home_vault) if home_vault.exists() else str(APP_DIR / "vault")
    safe = _safe_prompt(intent)     # 下面走 shell=True，只換掉 `"` 擋不住 $(...) / `...` / &
    if not safe:
        messagebox.showerror("CodexAutoAI", "請先在需求框輸入要開發的功能意圖。")
        return None
    last_detail = ""
    for cmd, extra_env in cands:
        env = dict(os.environ)
        env.update(extra_env)
        env["SPEC_VAULT"] = vault
        try:
            p = subprocess.run(f'{cmd} seed "{safe}"', shell=True, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", env=env, timeout=90)
        except Exception as exc:  # noqa: BLE001
            last_detail = str(exc)
            continue
        out = (p.stdout or "").strip()
        spec_path = out.splitlines()[-1].strip() if out else ""
        if p.returncode == 0 and spec_path.lower().endswith(".md"):
            # 路徑轉正斜線：它之後要穿過 `_safe_prompt`，反斜線會被清掉。
            return f"依照規格檔 {spec_path.replace(chr(92), '/')} 開發，跑完整七階段"
        last_detail = (p.stderr or p.stdout or "").strip()[:300]
    hint = ("疑似找不到 Python——請先按「設定 / 修復」。" if "python" in last_detail.lower()
            else "")
    messagebox.showerror("CodexAutoAI", f"產生 spec 失敗。{hint}{last_detail}")
    return None


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

    **路徑不要餵進來**（`\\` 會被刪掉）；呼叫端先把路徑轉成正斜線。
    JS 對應版是 `vscode-extension/prompt.js` 的 `safePrompt`，兩邊規則必須一致
    （`tests/test_launcher.py` 有 parity 測試把關）。
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
    # 外部終端機這條路以前寫死安裝目錄——內嵌那條改吃專案資料夾之後，只要使用者
    # 取消勾選內嵌、或這台機器 PTY 不可用，產出照樣會落回 App 的安裝目錄。
    root = prepare_project_dir()
    try:
        if IS_WIN:
            # claude 在 Windows 是 .cmd 蓋子，必須由 cmd 執行；argv list 交給
            # Popen 自行加引號，不自己拼字串。
            tail = ["cmd", "/k", "claude"] + ([prompt] if prompt else [])
            wt = _which("wt")
            if wt:             # 優先 Windows Terminal
                subprocess.Popen([wt, "-d", str(root)] + tail)
            else:
                # `start` 是 cmd 內建，需要 cmd 承載；第二個引數是視窗標題。
                subprocess.Popen(["cmd", "/c", "start", "CodexAutoAI"] + tail,
                                cwd=str(root))
        else:
            # 需求走位置參數 $2，永遠不被 bash 解析成程式碼。
            script = 'cd "$1" && exec claude ${2:+"$2"}'
            argv = ["bash", "-lc", script, "bash", str(root), prompt]
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
        root.geometry("560x900")
        root.minsize(520, 840)
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
        self.btn_title = tkfont.Font(family="Segoe UI", size=15, weight="bold")
        self.btn_sub = tkfont.Font(family="Segoe UI", size=9)
        self.rows: dict[str, dict] = {}
        self._update_info: dict | None = None
        self._app_started_at = time.time()
        self._build()
        self.refresh()
        self.poll_progress()
        self.start_update_check()

    def _build(self) -> None:
        # 版面：左邊是原本的控制欄（寬度固定 LEFT_W），右邊是內嵌終端機欄。
        # 右欄預設**不 pack**——沒開終端機時視窗就跟以前一樣是一條窄的控制欄，
        # 不會平白多出一塊空白。開了才 pack 進來並把視窗撐寬。
        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True)
        self.left = tk.Frame(self.body, bg=BG, width=LEFT_W)
        self.left.pack(side="left", fill="y")
        self.left.pack_propagate(False)   # 不讓子元件把固定寬度撐掉
        self._build_term_pane()

        tk.Label(self.left, text="CodexAutoAI", font=self.h1, fg=GOLD, bg=BG).pack(pady=(22, 2))
        tk.Label(self.left, text="一句話描述需求，自動跑完需求→架構→寫碼→測試→交付",
                 font=self.h2, fg=MUTED, bg=BG).pack(pady=(0, 16))

        # 更新橫幅（預設隱藏，背景檢查到新版才 pack 進來）
        self.update_banner = tk.Frame(self.left, bg="#2a2410")
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
        self.card = card = tk.Frame(self.left, bg=CARD)
        card.pack(fill="x", padx=22)
        tk.Label(card, text="環境檢查", font=self.h2, fg=CHAMPAGNE, bg=CARD).pack(anchor="w", padx=14, pady=(12, 6))
        for key, label, critical in check_rows():     # 靜態清單，不跑子行程
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", padx=14, pady=2)
            dot = tk.Label(row, text="●", font=self.h2, fg=MUTED, bg=CARD, width=2)
            dot.pack(side="left")
            name = tk.Label(row, text=label, font=self.h2, fg=CHAMPAGNE, bg=CARD, width=12, anchor="w")
            name.pack(side="left")
            msg = tk.Label(row, text="檢查中…", font=self.mono, fg=MUTED, bg=CARD, anchor="w")
            msg.pack(side="left", fill="x", expand=True)
            self.rows[key] = {"dot": dot, "msg": msg, "critical": critical}

        btns = tk.Frame(card, bg=CARD)
        btns.pack(fill="x", padx=14, pady=(8, 12))
        tk.Button(btns, text="🔧 設定 / 修復", command=self.on_setup, font=self.h2,
                  bg="#21262d", fg=CHAMPAGNE, relief="flat", padx=12, pady=4).pack(side="left")
        tk.Button(btns, text="↻ 重新檢查", command=self.refresh, font=self.h2,
                  bg="#21262d", fg=CHAMPAGNE, relief="flat", padx=12, pady=4).pack(side="left", padx=8)

        # 專案資料夾：pipeline 的產出（src/ tests/ docs/ log/）都落在這裡。
        # 以前寫死成安裝目錄，使用者的專案會被寫進 App 自己的安裝路徑。
        proj = tk.Frame(self.left, bg=CARD)
        proj.pack(fill="x", padx=22, pady=(14, 0))
        head = tk.Frame(proj, bg=CARD)
        head.pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(head, text="專案資料夾", font=self.h2, fg=CHAMPAGNE, bg=CARD).pack(side="left")
        tk.Button(head, text="📂 變更", command=self.on_pick_project, font=self.mono,
                  bg="#21262d", fg=CHAMPAGNE, relief="flat", padx=10).pack(side="right")
        self.proj_label = tk.Label(proj, text="", font=self.mono, fg=MUTED, bg=CARD,
                                   anchor="w", justify="left", wraplength=LEFT_W - 60)
        self.proj_label.pack(fill="x", padx=14, pady=(0, 10))
        self._refresh_project_label()

        # 需求 + 啟動
        tk.Label(self.left, text="你想做什麼？", font=self.h2, fg=CHAMPAGNE, bg=BG).pack(anchor="w", padx=24, pady=(18, 4))
        self.req = tk.Text(self.left, height=3, font=self.h2, bg=CARD, fg=CHAMPAGNE,
                          insertbackground=GOLD, relief="flat", wrap="word")
        self.req.pack(fill="x", padx=22)
        self.req.insert("1.0", "做一個記帳 CLI 工具，資料存 SQLite，將此任務作為一個測試 codex-auto-ai 自身功能的範例沙盒測試專案")

        # 執行模式：勾選＝非停（autopilot），連回合都不停一路跑到交付（commit/push 仍會問）。
        self.autopilot_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            self.left,
            text="非停模式（autopilot）：連回合都不停，一路跑到交付（commit/push 仍會問）",
            variable=self.autopilot_var, font=self.mono, fg=MUTED, bg=BG,
            activebackground=BG, activeforeground=CHAMPAGNE, selectcolor=CARD,
            anchor="w",
        ).pack(fill="x", padx=24, pady=(8, 0))

        # 內嵌終端機：預設開。關掉才會回到舊行為（每個 session 跳一個外部終端機視窗）。
        self.embed_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            self.left,
            text="內嵌終端機：session 開在 App 內、用分頁管理（取消勾選則另開外部終端機）",
            variable=self.embed_var, font=self.mono, fg=MUTED, bg=BG,
            activebackground=BG, activeforeground=CHAMPAGNE, selectcolor=CARD,
            anchor="w",
        ).pack(fill="x", padx=24, pady=(2, 0))

        # 唯一的啟動入口：先產規格再跑七階段。以前還有一顆「直接把需求丟給
        # claude」的按鈕，但那條路跳過了規格階段，等於繞開本框架的前半段；
        # 兩顆並排也只會讓人不知道該按哪顆。
        self.launch_btn = PrimaryButton(
            self.left, "啟動新任務", "先產規格 → 七階段自動開發",
            self.on_seed_from_spec, self.btn_title, self.btn_sub)
        self.launch_btn.pack(fill="x", padx=22, pady=(12, 8))
        self.seed_btn = self.launch_btn      # 停用/還原邏輯沿用同一顆

        # 直接開終端機（不建 session）：想手動開 codex 或一般 shell 分頁時用。
        self.term_btn = tk.Button(self.left, text="🖥 開啟內嵌終端機（分頁管理 Claude / Codex）",
                                  command=self.on_open_terminal, font=self.h2,
                                  bg="#21262d", fg=CHAMPAGNE, relief="flat", pady=6)
        self.term_btn.pack(fill="x", padx=22, pady=(0, 10))

        # 進度卡：讀 log/events.jsonl（共用 tools/events_model.py）。
        # 在這之前，App 按下啟動後就只是「開了一個終端機」——pipeline 跑到哪、
        # 卡在哪、花了多少，全都只能去終端機看。這張卡把它拉回 App 內。
        self.prog_card = tk.Frame(self.left, bg=CARD)
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

        self.status = tk.Label(self.left, text="", font=self.mono, fg=MUTED, bg=BG)
        self.status.pack()

    # ── 內嵌終端機欄 ────────────────────────────────────────────────────────
    def _build_term_pane(self) -> None:
        """右欄的殼。真正的終端機是一個被 SetParent 進 `term_host` 的瀏覽器視窗
        （見 `winembed.py`），所以這裡只準備容器與工具列。"""
        self.termpane = tk.Frame(self.body, bg=CARD)
        bar = tk.Frame(self.termpane, bg=CARD)
        bar.pack(fill="x", padx=10, pady=(8, 6))
        tk.Label(bar, text="內嵌終端機", font=self.h2, fg=CHAMPAGNE, bg=CARD).pack(side="left")
        # 這兩個也要能被停用：它們會 close() 掉 attach 正在用的那個外殼，而 attach
        # 期間 UI 是活的（靠 root.update() 泵事件），使用者真的按得到。
        self.popout_btn = tk.Button(bar, text="⇱ 另開視窗", command=self.on_pop_out,
                                    font=self.mono, bg="#21262d", fg=MUTED,
                                    relief="flat", padx=10)
        self.popout_btn.pack(side="right")
        self.collapse_btn = tk.Button(bar, text="⤢ 收合", command=self.hide_terminal_pane,
                                      font=self.mono, bg="#21262d", fg=MUTED,
                                      relief="flat", padx=10)
        self.collapse_btn.pack(side="right", padx=(0, 6))
        # 內嵌用的宿主 frame。背景刻意跟終端機頁面同色，這樣瀏覽器還沒畫出來的
        # 那一瞬間不會閃一塊亮色。
        self.term_host = tk.Frame(self.termpane, bg="#0f1115")
        self.term_host.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.term_host.bind("<Configure>", self._on_term_resize)
        # 點終端機區就把鍵盤焦點交給嵌進來的瀏覽器——reparent 進別的行程之後，
        # 前景視窗是 tk 的 toplevel，按鍵預設進 tk 的佇列，不會自己過去。
        self.term_host.bind("<Button-1>", self._focus_embed, add="+")
        self.termpane.bind("<Button-1>", self._focus_embed, add="+")
        # tk 要收得到鍵盤才轉送得出去
        self.term_host.config(takefocus=True)
        self.term_host.bind("<Key>", self._on_term_key, add="+")
        # 鍵盤狀態指示：使用者回報「打不了字」時，這一行能直接分辨是「App 沒接管
        # 到鍵盤」還是「接管了但對面的 CLI 正在忙、不收輸入」——否則只能猜。
        self.kbd_label = tk.Label(self.termpane, text="", font=self.mono,
                                  fg=MUTED, bg=CARD, anchor="w")
        self.kbd_label.pack(fill="x", padx=10, pady=(0, 6))
        self._keep_keyboard()

        self._embed = None
        self._resize_job = None
        self._embed_busy = False      # attach 進行中；擋 root.update() 造成的重入
        self._btn_state: dict[str, str] = {}
        # 只註冊**一次**收尾。改成每次 attach 都 add_closer 的話，開開合合幾十次
        # 之後就是幾十個指向已關閉物件的 callback，App 活多久就漲多久。
        TERMINAL.add_closer(self._close_embed)

    def show_terminal_pane(self, open_url) -> bool:
        """把終端機頁面嵌進右欄。回 False 代表這台機器嵌不了，呼叫端自行退回瀏覽器。

        **必須擋重入**：`attach()` 會等瀏覽器視窗出現（最多 ~25 秒），期間靠
        `root.update()` 讓 UI 不凍住——而 `root.update()` 會把事件迴圈再跑一輪，
        使用者這時再按一次按鈕，第二個 callback 就會在第一個還沒回來時進來，
        變成同時開兩個瀏覽器外殼（第一個還會變成沒人收的孤兒）。
        """
        ok, why = winembed.available() if winembed else (False, "缺少 winembed 模組")
        if not ok:
            self.status.config(text=f"視窗內嵌不可用（{why}），改用瀏覽器開啟", fg=GOLD)
            _note_embed(f"不可用：{why}")
            return False
        if self._embed_busy:
            self.status.config(text="內嵌終端機正在開啟中…", fg=GOLD)
            return True                    # 回 True＝已處理，別再退回去開瀏覽器
        if self._embed is not None and self._embed.alive:
            # 已經嵌好了，收合過就再展開。**不用重新導向**：剛剛在 open() 裡建的
            # session 由頁面自己的輪詢（terminal.html 的 syncSessions）接上；
            # 重新導向反而會把正在跑的畫面洗掉。
            self._expand_window()
            return True

        self._embed_busy = True
        self._set_actions_enabled(False)
        try:
            self._expand_window()
            self.status.config(text="正在開啟內嵌終端機…", fg=GOLD)
            self.root.update()             # 先讓 frame 有真正的尺寸與 HWND
            emb = winembed.EmbeddedBrowser()
            # **在 attach 之前就掛上去**：attach 最長等 25 秒，期間靠 root.update()
            # 泵事件迴圈——使用者這時按關閉鈕，on_close() 會 destroy root，之後
            # attach 裡的 pump() 與這裡的 status.config() 都會拋 TclError，一路被
            # open() 外層的 except 吞掉，emb.close() 永遠不會被呼叫，Popen 出去的
            # Chromium 就變孤兒。先賦值的話，關閉路徑的 _close_embed() 收得到它。
            self._embed = emb
            okd = emb.attach(self.term_host.winfo_id(), open_url(),
                             width=max(self.term_host.winfo_width(), 600),
                             height=max(self.term_host.winfo_height(), 400),
                             pump=self.root.update)
            if not okd:
                self._close_embed()
                self._collapse_window()
                self.status.config(text=f"視窗內嵌失敗（{emb.error}），改用瀏覽器開啟",
                                   fg=GOLD)
                _note_embed(f"失敗：{emb.error}")
                return False
            emb.focus()          # 省得使用者還要多點一下才能打字
            self.status.config(text="終端機已嵌在右邊欄位", fg=GREEN)
            return True
        finally:
            self._embed_busy = False
            self._set_actions_enabled(True)

    def _set_actions_enabled(self, on: bool) -> None:
        """開啟期間把會再觸發一次的按鈕停掉（重入防線的第二層，也讓使用者看得出在忙）。

        「啟動新任務」的正常狀態由 `refresh()` 依環境檢查決定，所以這裡記住原本的
        狀態再還原，不要自作主張把它打開。
        """
        # 每個名字必須對應**不同**的 widget：同一顆掛兩個名字的話，第一次讀到的是
        # 原本狀態、第二次讀到的已經是 disabled，還原時就會把它留在 disabled。
        for name in ("launch_btn", "term_btn", "collapse_btn", "popout_btn"):
            btn = getattr(self, name, None)
            if btn is None:
                continue
            if not on:
                self._btn_state[name] = btn.cget("state")
                btn.config(state="disabled")
            else:
                btn.config(state=self._btn_state.pop(name, "normal"))

    def _close_embed(self) -> None:
        """收掉目前嵌著的瀏覽器外殼（沒有就當沒事）。App 關閉時也走這裡。"""
        emb, self._embed = self._embed, None
        if emb is not None:
            emb.close()

    def hide_terminal_pane(self) -> None:
        """收合右欄。**session 不會被關掉**——服務的生命週期跟著 App，重新展開會接回。"""
        self._close_embed()
        self._collapse_window()
        self.status.config(text="已收合終端機（session 仍在執行，可再開啟接回）", fg=MUTED)

    def on_pop_out(self) -> None:
        """把終端機丟回獨立視窗——想把它拉到第二螢幕時用。

        重開一次 `TERMINAL.open()` 是為了拿一張新的 handoff 券——舊那張已經被嵌
        進來的那個頁面用掉了，拿它去開新視窗只會開出一個沒有 token 的頁面。
        """
        self._close_embed()
        self._collapse_window()
        TERMINAL.open()          # 不傳 shell → 直接走獨立視窗那條路
        self.status.config(text="已改用獨立視窗開啟終端機", fg=GREEN)

    def _expand_window(self) -> None:
        if self.termpane.winfo_ismapped():
            return
        self.termpane.pack(side="right", fill="both", expand=True)
        self.root.minsize(LEFT_W + 360, 840)
        # 只在「還沒被使用者手動撐寬過」時才自己加寬，否則會把使用者調好的尺寸蓋掉。
        if self.root.winfo_width() <= LEFT_W + 40:
            self.root.geometry(f"{LEFT_W + TERM_W}x{max(self.root.winfo_height(), 900)}")

    def _collapse_window(self) -> None:
        if not self.termpane.winfo_ismapped():
            return
        self.termpane.pack_forget()
        self.root.minsize(520, 840)
        self.root.geometry(f"{LEFT_W}x{max(self.root.winfo_height(), 900)}")

    def _focus_embed(self, _event=None) -> None:
        """點終端機區時把鍵盤焦點留在 **tk**，由 `_on_term_key` 轉送給 pty。

        以前這裡是把焦點交給嵌進來的瀏覽器，但那條路走不通：實測頁面回報
        `hasFocus()=true`、`activeElement` 也是 xterm 的 textarea，打字卻毫無反應
        ——按鍵在到達 renderer 之前就被丟掉了。改成 tk 自己收、直接寫進 pty。
        """
        self.term_host.focus_force()

    def _keep_keyboard(self) -> None:
        """把鍵盤焦點留在 tk，這樣 `_on_term_key` 才收得到。

        **點終端機不會觸發 tk 的 `<Button-1>`**：嵌進來的瀏覽器視窗整片蓋在
        `term_host` 上，點擊由它接走、順便把 OS 焦點也拿走，tk 從此收不到按鍵。
        所以改成輪詢：只要焦點跑到 tk 的元件之外（`focus_get()` 是 None）而 App
        又還在最前面，就把它搶回來。

        **`app_is_foreground` 這道守門不能省**：少了它，使用者切到別的程式時我們
        會每 300ms 把焦點硬拉回來，變成搶使用者的鍵盤。
        """
        try:
            emb = self._embed
            if (emb is not None and emb.alive and emb.hwnd
                    and self.termpane.winfo_ismapped()
                    and self.root.focus_get() is None
                    and winembed is not None
                    and winembed.app_is_foreground(emb.hwnd)):
                self.term_host.focus_force()
            self._update_kbd_hint()
        except Exception:  # noqa: BLE001 — 焦點顧不到不該讓 App 出錯
            pass
        self.root.after(300, self._keep_keyboard)

    def _update_kbd_hint(self) -> None:
        lab = getattr(self, "kbd_label", None)
        if lab is None:
            return
        emb = self._embed
        if emb is None or not emb.alive or not self.termpane.winfo_ismapped():
            lab.config(text="")
            return
        sess = self._current_session()
        if self.root.focus_get() is self.term_host and sess is not None:
            lab.config(text=f"⌨ 鍵盤已接管 → {sess.kind.label} · {sess.id}"
                            f"（打不出字代表對面的 CLI 正在忙）", fg=GREEN)
        else:
            lab.config(text="⌨ 鍵盤未接管 — 點一下終端機區", fg=GOLD)

    def _current_session(self):
        """鍵盤要送到哪條 session。

        面板可以同時開好幾格，但 tk 不知道網頁裡哪一格有焦點，所以送給**最新建立
        且還活著**的那條——單一 pipeline 的常見情境下就是使用者正在看的那條。
        """
        mgr = getattr(TERMINAL, "_mgr", None)
        if mgr is None:
            return None
        alive = [s for s in mgr.list() if s.alive]
        return alive[-1] if alive else None

    def _on_term_key(self, event) -> str:
        """把 tk 收到的按鍵轉送進 pty。回 "break" 讓 tk 不要再自己處理。"""
        sess = self._current_session()
        if sess is None:
            return ""
        ctrl = bool(event.state & 0x0004)
        data = keystroke_to_bytes(event.keysym, event.char or "", ctrl)
        if not data:
            return ""
        try:
            sess.write(data)
        except Exception:  # noqa: BLE001 — session 剛死之類，不該讓 UI 出錯
            return ""
        return "break"

    def _on_term_resize(self, _event=None) -> None:
        """跟著欄位調整內嵌視窗大小。

        **debounce 是必要的**：拖曳視窗邊框時 `<Configure>` 每幾毫秒就來一次，
        每次都去 `SetWindowPos` + 強制重繪會讓拖曳整個卡住。
        """
        if self._embed is None:
            return
        if self._resize_job is not None:
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:  # noqa: BLE001
                pass
        self._resize_job = self.root.after(80, self._apply_term_size)

    def _apply_term_size(self) -> None:
        self._resize_job = None
        if self._embed is None:
            return
        if not self._embed.alive:          # 使用者從工作管理員砍掉之類
            self._embed = None
            return
        self._embed.fit(self.term_host.winfo_width(), self.term_host.winfo_height())

    # ── 進度輪詢 ────────────────────────────────────────────────────────────
    def _progress_is_stale(self) -> bool:
        """這份進度是不是「這次開 App 之前」留下的。

        抽成獨立函式是為了測得到——原本寫在 `poll_progress()` 裡，測試只能驗
        `_events_mtime()` 這種輔助函式，把判斷改成 `False` 照樣全綠（變異測試抓不到）。
        """
        return self._events_mtime() < getattr(self, "_app_started_at", 0)

    def _events_mtime(self) -> float:
        try:
            return self._events_log().stat().st_mtime
        except OSError:
            return 0.0

    def _events_when(self) -> str:
        """事件檔最後更新的時間，讓使用者一眼看出這份進度有多舊。"""
        ts = self._events_mtime()
        if not ts:
            return ""
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime("%m/%d %H:%M")

    def _events_log(self) -> Path:
        return active_project_dir() / "log" / "events.jsonl"

    def _run_finished(self, model) -> bool:
        """這次 App 啟動的任務跑完了嗎。

        **不能只看 `model["state"]`**：剛按下啟動的那一刻，`events.jsonl` 還是上一輪
        留下的，狀態多半已經是 done——下一次輪詢（2 秒後）就會把守門員收膛，等於
        整個保護在最需要的時候消失。所以還要求「事件檔在我們啟動之後真的被寫過」，
        確認看到的是這一輪的結果而不是上一輪的殘影。
        """
        if not model or model.get("state") not in {"done", "escalated"}:
            return False
        try:
            return self._events_log().stat().st_mtime > getattr(self, "_app_run_at", 0)
        except OSError:
            return False

    def poll_progress(self) -> None:
        """每 2 秒讀一次 events.jsonl 更新進度卡（唯讀，不干擾 pipeline）。"""
        try:
            model = load_progress_model(self._events_log())
        except Exception:  # noqa: BLE001
            model = None
        if getattr(self, "_app_run", False):
            if self._run_finished(model):
                # 跑完就收膛，跟 state.json 那條路的語意一致（`build 已結束一律放行`）。
                # 不收的話，交付之後只要 App 還開著，Claude 連手動改一行都會被擋，
                # 而且沒有任何徵兆。
                self._app_run = False
            else:
                touch_app_run(active_project_dir())   # 心跳：App 還開著＝任務還在跑



        if not model or not model.get("log_exists"):
            self.prog_bar.config(text="尚未開始", fg=MUTED)
            self.prog_detail.config(text="按上面的按鈕啟動，這裡會顯示 Phase 進度")
            self.abort_btn.config(state="disabled", fg=MUTED)
        else:
            bar = "".join("▓" if i <= model["marker"] else "░"
                          for i in range(model["total"] + 1))
            colour = {"escalated": RED, "done": GREEN}.get(model["state"], GOLD)
            # **標明這是不是「這次開 App 之後」的進度。** 事件檔留在專案資料夾裡，
            # 所以重開 App 會直接看到上一輪跑到哪——使用者無從分辨那是現在還是上次的，
            # 會以為任務正在跑。用事件檔的更新時間跟 App 啟動時間比對。
            stale = self._progress_is_stale()
            prefix = "上次執行　" if stale else ""
            if stale:
                colour = MUTED
            self.prog_bar.config(
                text=f"{prefix}Phase {model['marker']}/{model['total']} {bar} {model['current_name']}",
                fg=colour)
            # 不再列「已完成：[0, 1, 2]」——上面的進度條已經表達同一件事，
            # 再用一串裸數字重複一次只是佔位子又難看。
            bits = []
            if model["iteration"]:
                bits.append(f"迭代 {model['iteration']}")
            if model["cost_usd"]:
                bits.append(f"${model['cost_usd']:.4f}")
            if model["errors"]:
                bits.append(f"錯誤：{model['errors'][-1]['reason']}")
            # 沒有細節可講時的墊字要跟狀態一致——一律寫「執行中…」的話，
            # 跑完了下面還說執行中，跟上面那條變綠的進度條互相矛盾。
            idle = {"done": "已完成", "escalated": "已中止"}.get(model["state"], "執行中…")
            when = self._events_when()
            detail = "　".join(bits) or idle
            self.prog_detail.config(text=f"{detail}　{when}" if when else detail,
                                    fg=MUTED if stale else MUTED)
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
            flag = active_project_dir() / "log" / "abort.flag"
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("", encoding="utf-8")
            self.status.config(text="已送出中止，將於下一個回合邊界停止", fg=GOLD)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("CodexAutoAI", f"寫入中止旗標失敗：{exc}")

    def refresh(self) -> None:
        """重新做環境檢查。**在背景執行緒跑**，UI 立刻可用。

        `gather_checks()` 會叫起好幾個子行程（`codex login status`、`gh auth status`、
        探 python 版本…），每個都有秒級的逾時。以前是在 `__init__` 裡同步跑完才畫
        UI，所以 App 開起來要卡好幾秒才點得到輸入框——而且每加一項檢查就更慢。
        """
        if getattr(self, "_checking", False):
            return
        self._checking = True
        for r in self.rows.values():
            r["dot"].config(fg=MUTED)
            r["msg"].config(text="檢查中…")
        self.status.config(text="正在檢查環境…", fg=MUTED)

        def worker() -> None:
            try:
                result = gather_checks()
            except Exception:  # noqa: BLE001
                result = []
            # **不從工作執行緒碰 tkinter**（連 `after` 都不要）：Tcl 直譯器不是
            # thread-safe，跨執行緒呼叫是未定義行為。放進格子，讓主執行緒自己來拿。
            self._checks_result = result

        self._checks_result = None
        threading.Thread(target=worker, daemon=True).start()
        self._poll_checks()

    def _poll_checks(self) -> None:
        """主執行緒這邊等背景檢查的結果。"""
        result = getattr(self, "_checks_result", None)
        if result is None:
            self.root.after(150, self._poll_checks)
            return
        self._checks_result = None
        self._apply_checks(result)

    def _apply_checks(self, checks: list) -> None:
        self._checking = False
        if not checks:
            self.status.config(text="環境檢查失敗，請按「↻ 重新檢查」", fg=RED)
            return
        ready = True
        for c in checks:
            r = self.rows.get(c["key"])
            if not r:
                continue
            r["dot"].config(fg=GREEN if c["ok"] else (RED if c["critical"] else GOLD))
            r["msg"].config(text=c["msg"])
            if c["critical"] and not c["ok"]:
                ready = False
        if ready:
            self.launch_btn.config(state="normal")
            self.status.config(text="✓ 環境就緒，可以啟動", fg=GREEN)
        else:
            self.launch_btn.config(state="disabled")
            self.status.config(text="請先按「設定 / 修復」完成 Claude / Codex 登入", fg=RED)

    def _refresh_project_label(self) -> None:
        self.proj_label.config(text=str(project_dir()))

    def on_pick_project(self) -> None:
        """換專案資料夾。

        已經有 session 在跑時**不換**——換了只會讓新舊 session 跑在不同目錄、
        進度卡看的又是第三個地方，比不給換更難懂。
        """
        from tkinter import filedialog  # noqa: PLC0415 — 只有按下去才需要
        # `has_live_sessions()` 只看得到內嵌的 session；外部終端機那條路 App 完全
        # 觀察不到，所以再看一次上膛旗標，否則換了資料夾之後舊目錄的標記會在
        # 180 秒內過期，而外部終端機還在寫 src/。
        if TERMINAL.has_live_sessions() or getattr(self, "_app_run", False):
            messagebox.showinfo(
                "CodexAutoAI",
                "還有 session 在執行中。請先關掉它們再換專案資料夾——"
                "否則新舊 session 會跑在不同目錄。")
            return
        picked = filedialog.askdirectory(title="選擇專案資料夾",
                                         initialdir=str(project_dir()))
        if not picked:
            return
        set_project_dir(Path(picked))
        # 快取的解析結果也要作廢，否則 `active_project_dir()`（心跳 / 進度卡 /
        # 中止旗標）會繼續指著**舊**資料夾，跟 session 實際跑的地方分家。
        global _RESOLVED_ROOT
        _RESOLVED_ROOT = None
        # 也要收膛：不收的話下一次心跳（2 秒後）會在**新的**資料夾寫下標記，
        # 那裡根本還沒啟動過任何任務，守門員卻已經上膛。
        self._app_run = False
        TERMINAL.reset_workdir()
        self._refresh_project_label()
        self.status.config(text=f"專案資料夾已改為 {picked}", fg=GREEN)

    def on_setup(self) -> None:
        """環境 pre-check（與 extension skipSetupWhenReady 一致）：關鍵項都就緒就不開終端機。

        檢查同樣丟到背景——它跟啟動時跑的是同一批子行程，在 UI 執行緒上同步跑會讓
        「設定 / 修復」按下去整個卡住，正是這次要修掉的那個毛病。
        """
        # 重入防線：改成非同步之後，卡頓時使用者連點兩下就會開出**兩個**設定視窗、
        # 甚至同時跑兩份安裝流程（`run_setup()` 每次都 Popen 一個新終端機）。
        if getattr(self, "_setting_up", False):
            return
        self._setting_up = True
        self.status.config(text="正在檢查環境…", fg=MUTED)

        def worker() -> None:
            try:
                self._setup_checks = gather_checks()
            except Exception:  # noqa: BLE001
                self._setup_checks = []

        self._setup_checks = None
        threading.Thread(target=worker, daemon=True).start()
        self._poll_setup()

    def _poll_setup(self) -> None:
        checks = getattr(self, "_setup_checks", None)
        if checks is None:
            self.root.after(150, self._poll_setup)
            return
        self._setup_checks = None
        self._setting_up = False
        # 直接把剛拿到的結果套上去，不要再叫 refresh() 重跑一輪——那等於一次點擊
        # 跑兩遍 `codex login status` / `gh auth status`，正是這個 PR 在減的開銷。
        self._apply_checks(checks)
        missing = [c for c in checks if c["critical"] and not c["ok"]]
        if checks and not missing:
            self.status.config(text="✓ Claude / Codex 已安裝並登入，無需重跑設定", fg=GREEN)
            return
        run_setup()
        self.status.config(text="設定視窗已開啟，完成後請按「↻ 重新檢查」", fg=GOLD)

    def _start_task(self, req: str) -> None:
        """把需求交出去執行。**內嵌面板優先**，PTY 不可用才退回外部終端機。

        「啟動新任務」與「從 spec 開始」共用這一段——以前 spec 那條路自己直接呼叫
        `launch_claude()`，所以永遠開在視窗外的原生終端機，兩顆按鈕行為不一致。
        """
        autopilot = self.autopilot_var.get()
        mode = "非停（autopilot）" if autopilot else "一般"
        # 先上膛再啟動：守門員要在 Claude 有機會寫第一個檔之前就生效
        self._app_run = True
        self._app_run_at = time.time()
        touch_app_run(prepare_project_dir(), prompt=req)

        if self.embed_var.get():
            ok, why = TERMINAL.available()
            if ok:
                prompt = f"/autopilot on {_safe_prompt(req)}".strip() if autopilot \
                    else _safe_prompt(req)
                if TERMINAL.open(kind="claude", prompt=prompt,
                                 shell=self.show_terminal_pane):
                    self.status.config(
                        text=f"已在內嵌終端機開啟 Claude 分頁（{mode}）", fg=GREEN)
                    return
                # 建 session 失敗（claude 不見了、session 數到上限、pty 起不來）。
                # 錯誤訊息 open() 已經跳過了，這裡只要收膛＋別謊報成功。
                self._app_run = False
                self.status.config(text="開啟 Claude session 失敗，任務未啟動", fg=RED)
                return
            # 不可用就誠實說明並降級，不要靜默地換一種行為
            self.status.config(text=f"內嵌終端機不可用（{why}），改用外部終端機", fg=GOLD)

        if launch_claude(req, autopilot=autopilot):
            self.status.config(text=f"已開啟外部終端機（{mode}），在新視窗執行中…", fg=GREEN)
        else:
            # 沒啟動成功就要收膛。上膛刻意放在啟動**之前**（守門員要趕在 Claude 有
            # 機會寫第一個檔之前生效），代價就是失敗路徑必須自己收回來——否則那個
            # 資料夾會一直上著膛卻沒有任何任務在跑，而且完全沒有徵兆。
            self._app_run = False

    def on_open_terminal(self) -> None:
        """只開終端機視窗，不建 session——使用者自己在裡面按「＋ 新增」。

        **刻意不上膛**（與「啟動新任務」「從 spec 開始」不同）：這裡只是開一個空
        面板，使用者可能只是想開個 shell 看東西。在這裡上膛的話，守門員會一直
        掛著到 App 關閉為止（沒有 pipeline 事件可以判斷「跑完了」），連帶把純
        shell 用途也擋住。從面板手動開的 session 與下拉選單的 Codex 一樣，屬於
        自負其責的手動逃生口。
        """
        ok, why = TERMINAL.available()
        if not ok:
            messagebox.showerror("CodexAutoAI", f"內嵌終端機不可用：{why}")
            return
        TERMINAL.open(shell=self.show_terminal_pane)
        self.status.config(text="已開啟內嵌終端機（可用分頁管理多個 session）", fg=GREEN)

    def on_seed_from_spec(self) -> None:
        """主要入口：一句意圖 → gs-spec-forge 產規格 → 在內嵌面板跑七階段。"""
        intent = self.req.get("1.0", "end").strip()
        self.status.config(text="spec-forge 產生 spec 中…", fg=GOLD)
        self.root.update_idletasks()
        req = seed_from_spec(intent)
        if not req:
            self.status.config(text="產生 spec 失敗，任務未啟動", fg=RED)
            return
        # 走跟「直接啟動」同一條路：內嵌面板優先，不可用才退回外部終端機。
        self._start_task(req)

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
        # 內嵌終端機的 pty 子行程（claude 底下還有 node）不收會變孤兒，
        # 所以關閉路徑一定要一併收掉，且要放在 overlay 還原之前——
        # 就算還原失敗也已經先把行程收乾淨。
        try:
            TERMINAL.shutdown()
        except Exception:  # noqa: BLE001
            pass
        if released["done"] or global_overlay is None:
            return
        released["done"] = True
        try:
            global_overlay.release(_overlay_token())
        except Exception:  # noqa: BLE001 — 還原失敗不該擋住關閉
            pass

    import atexit
    atexit.register(release_overlay)   # 沒有 overlay 時也要收 session
    if global_overlay is not None:
        try:
            global_overlay.acquire(_overlay_token())
        except Exception:  # noqa: BLE001 — 套用失敗不該擋住啟動
            pass

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
