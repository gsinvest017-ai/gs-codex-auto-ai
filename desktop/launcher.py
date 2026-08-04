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
            root = project_dir()
            try:
                root.mkdir(parents=True, exist_ok=True)
            except Exception:  # noqa: BLE001 — 建不出來就退回安裝目錄，別讓 App 開不起來
                root = APP_DIR
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
        try:
            major, minor = (int(x) for x in out.strip().splitlines()[-1].split())
        except Exception:  # noqa: BLE001 — 版本判讀不出來就別擋人，有 python 比沒有好
            return True, f"已安裝（{path}，版本判讀失敗）"
        if (major, minor) < MIN_PY:
            # **記下來繼續找下一個候選**，不能就此放棄：Linux 上 `python` 常常是
            # 舊的、真正能用的是 `python3`。這項現在是 critical，提早 return 會
            # 讓一台其實裝了 3.12 的機器按不下「啟動」。
            too_old = too_old or (f"版本太舊 {major}.{minor}（需要 "
                                  f"{MIN_PY[0]}.{MIN_PY[1]}+）— hooks 會失效")
            continue
        return True, f"已安裝（{path}，{major}.{minor}）"
    return False, too_old or "未安裝 — hooks 需要它，缺了 Codex-first 守門員會失效"


def gather_checks() -> list[dict]:
    claude_ok, claude_msg = check_claude()
    codex_ok, codex_msg = check_codex()
    node_ok, node_msg = check_simple("node", "Node.js（Codex 需要）")
    git_ok, git_msg = check_simple("git", "Git")
    gh_ok, gh_msg = check_gh()
    py_ok, py_msg = check_python()
    return [
        {"key": "claude", "name": "Claude Code", "ok": claude_ok, "msg": claude_msg, "critical": True},
        {"key": "codex", "name": "OpenAI Codex", "ok": codex_ok, "msg": codex_msg, "critical": True},
        {"key": "node", "name": "Node.js", "ok": node_ok, "msg": node_msg, "critical": False},
        {"key": "git", "name": "Git", "ok": git_ok, "msg": git_msg, "critical": False},
        {"key": "gh", "name": "GitHub CLI", "ok": gh_ok, "msg": gh_msg, "critical": False},
        # critical：hooks 全靠它，缺了整套 Codex-first 保證會靜默失效（見 check_python）
        {"key": "python", "name": "Python", "ok": py_ok, "msg": py_msg, "critical": True},
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


def seed_from_spec(intent: str) -> bool:
    """先用 gs-spec-forge 產 spec，再把 spec 當需求丟進既有 launch_claude（純附加）。

    候選鏈見 _spec_forge_candidates()；SPEC_VAULT 指定 vault（預設 ~/gs-vault，
    否則 App 目錄下 vault/）。
    """
    cands = _spec_forge_candidates()
    if not cands:
        messagebox.showerror(
            "CodexAutoAI",
            "找不到 spec-forge 也沒有內建快照。請先安裝 gs-spec-forge（跑其 install-spec-forge.ps1），"
            "或設環境變數 SPECFORGE_CMD 指到 spec-forge 執行檔。")
        return False
    vault = os.environ.get("SPEC_VAULT")
    if not vault:
        home_vault = Path.home() / "gs-vault"
        vault = str(home_vault) if home_vault.exists() else str(APP_DIR / "vault")
    safe = _safe_prompt(intent)     # 下面走 shell=True，只換掉 `"` 擋不住 $(...) / `...` / &
    if not safe:
        messagebox.showerror("CodexAutoAI", "請先在需求框輸入要開發的功能意圖。")
        return False
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
            # 路徑轉正斜線：它要穿過 launch_claude 的 _safe_prompt，反斜線會被清掉。
            return launch_claude(
                f"依照規格檔 {spec_path.replace(chr(92), '/')} 開發，跑完整七階段")
        last_detail = (p.stderr or p.stdout or "").strip()[:300]
    hint = ("疑似找不到 Python——請先按「設定 / 修復」。" if "python" in last_detail.lower()
            else "")
    messagebox.showerror("CodexAutoAI", f"產生 spec 失敗。{hint}{last_detail}")
    return False


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
    root = project_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        root = APP_DIR
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
        self.rows: dict[str, dict] = {}
        self._update_info: dict | None = None
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
        self.req.insert("1.0", "做一個記帳 CLI 工具，資料存 SQLite")

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

        self.launch_btn = tk.Button(self.left, text="🚀 啟動新任務", command=self.on_launch,
                                    font=self.h1, bg=GOLD, fg=BG, relief="flat", pady=8)
        self.launch_btn.pack(fill="x", padx=22, pady=(10, 6))

        # 啟動新任務：從 spec 開始（gs-spec-forge 整合，純附加）：先產 spec 再跑同一條 pipeline。
        self.seed_btn = tk.Button(self.left, text="▶ 啟動新任務：從 spec 開始（gs-spec-forge）",
                                  command=self.on_seed_from_spec, font=self.h2,
                                  bg="#21262d", fg=CHAMPAGNE, relief="flat", pady=6)
        self.seed_btn.pack(fill="x", padx=22, pady=(0, 6))

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
        for name in ("launch_btn", "seed_btn", "term_btn",
                     "collapse_btn", "popout_btn"):
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
    def _events_log(self) -> Path:
        return project_dir() / "log" / "events.jsonl"

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
                touch_app_run(project_dir())   # 心跳：App 還開著＝任務還在跑



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
            flag = project_dir() / "log" / "abort.flag"
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("", encoding="utf-8")
            self.status.config(text="已送出中止，將於下一個回合邊界停止", fg=GOLD)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("CodexAutoAI", f"寫入中止旗標失敗：{exc}")

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
        # 也要收膛：不收的話下一次心跳（2 秒後）會在**新的**資料夾寫下標記，
        # 那裡根本還沒啟動過任何任務，守門員卻已經上膛。
        self._app_run = False
        TERMINAL.reset_workdir()
        self._refresh_project_label()
        self.status.config(text=f"專案資料夾已改為 {picked}", fg=GREEN)

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
        """啟動新任務。預設走**內嵌終端機**（分頁管理），PTY 不可用才退回外部終端機。"""
        req = self.req.get("1.0", "end").strip()
        autopilot = self.autopilot_var.get()
        mode = "非停（autopilot）" if autopilot else "一般"
        # 先上膛再啟動：守門員要在 Claude 有機會寫第一個檔之前就生效
        self._app_run = True
        self._app_run_at = time.time()
        touch_app_run(project_dir(), prompt=req)

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
        """只開終端機視窗，不建 session——使用者自己在裡面按「＋ 新增」。"""
        ok, why = TERMINAL.available()
        if not ok:
            messagebox.showerror("CodexAutoAI", f"內嵌終端機不可用：{why}")
            return
        TERMINAL.open(shell=self.show_terminal_pane)
        self.status.config(text="已開啟內嵌終端機（可用分頁管理多個 session）", fg=GREEN)

    def on_seed_from_spec(self) -> None:
        intent = self.req.get("1.0", "end").strip()
        self.status.config(text="spec-forge 產生 spec 中…", fg=GOLD)
        self.root.update_idletasks()
        if seed_from_spec(intent):
            self.status.config(text="已產 spec 並開啟終端機跑 pipeline…", fg=GREEN)

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
