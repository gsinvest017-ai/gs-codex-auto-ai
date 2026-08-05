#!/usr/bin/env python3
"""
winembed.py — 把一個 Chromium 視窗 reparent 進 tkinter 的 frame，**純標準庫**。

## 為什麼需要這層

內嵌終端機（`termserver.py`）是一個本機 HTTP 服務 + xterm.js 頁面，原本只能
「另開瀏覽器分頁」或「pywebview 另開原生視窗」——兩種都是**離開 App 的視窗**，
使用者按下按鈕看到的是瀏覽器跳出來，session 又散回桌面各處，等於沒解決當初
要內嵌的問題。

pywebview 沒辦法嵌進既有視窗（[#405]、[#1141] 都是同一件事：它一定自己
`create_window`），所以走 Win32 的老路：用 `--app=<url>` 開一個無分頁列的
Chromium 視窗，把它的 style 從 popup 改成 child，`SetParent` 到 tk frame 的
HWND 上。`tkwebview2` 也是這樣做的。

[#405]: https://github.com/r0x0r/pywebview/issues/405
[#1141]: https://github.com/r0x0r/pywebview/discussions/1141

## 兩個實測踩到的坑（會反映在下面的程式碼）

1. **reparent / resize 後不重畫**。`MoveWindow` 會把外框調到正確尺寸，但
   WebView2 的內容不一定跟著重繪，畫面會留下上一輪的殘影（實測：外框
   896x600，只畫了約 525x495，其餘是螢幕殘留像素）。所以每次 resize 後都
   補一發 `SetWindowPos(..., SWP_FRAMECHANGED)` + `RedrawWindow(RDW_ALLCHILDREN)`。
   相關已知問題見 [WebView2Feedback#985]。

2. **視窗歸屬要指名道姓**。第一版 spike 用「任何一個 msedge.exe 的視窗」去比對，
   結果抓到的是上一輪失敗殘留的 Edge，不是這次啟動的那個。這裡改成
   **獨立 `--user-data-dir` + 只認我們 Popen 的那個 pid**（獨立 profile 會讓 Edge
   起自己的 browser process，不會併進既有實例），比對不到才退而用 URL nonce
   掃標題。

[WebView2Feedback#985]: https://github.com/MicrosoftEdge/WebView2Feedback/issues/985

只在 Windows 有效；其他平台 `available()` 回 False，呼叫端自行退回瀏覽器。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

IS_WIN = os.name == "nt"

__all__ = ["available", "browser_path", "build_argv", "EmbeddedBrowser", "NONCE_PARAM"]

# URL 上帶的識別參數。頁面會把它塞進 document.title，這樣「用標題找視窗」的
# 退路在頁面載入完之後仍然有效（載入中標題是 URL，本來就含有它）。
NONCE_PARAM = "embed"

# Edge 首次以全新 profile 啟動會跳「同步您的瀏覽資料」的登入卡，蓋住整個終端機。
# 這些旗標把首次體驗 / 隱性登入 / 同步 / 翻譯列全部關掉——內嵌視窗只是個殼，
# 不該有任何瀏覽器自己的 UI。
_QUIET_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-background-networking",
    "--disable-features=Translate,msImplicitSignin,msEdgeIdentity,"
    "msFirstRunExperience,msSpartanSignIn,EdgeDiscoverFeature",
]

# 找瀏覽器的順序：Edge 在 Windows 10/11 是內建的，最不會漏；找不到才看 Chrome。
_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def browser_path() -> str | None:
    """找一個能用 `--app=` 開無框視窗的 Chromium。找不到回 None。"""
    for p in _CANDIDATES:
        if os.path.exists(p):
            return p
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def available() -> tuple[bool, str]:
    """這台機器能不能把終端機嵌進 App 視窗裡。"""
    if not IS_WIN:
        return False, "視窗內嵌目前只支援 Windows"
    if browser_path() is None:
        return False, "找不到 Microsoft Edge 或 Chrome"
    return True, ""


def build_argv(browser: str, url: str, profile_dir: str,
               size: tuple[int, int] = (900, 600)) -> list[str]:
    """組出啟動指令。獨立成函式是為了能在沒有 Windows 的 CI 上測。"""
    return [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        f"--window-size={size[0]},{size[1]}",
        *_QUIET_FLAGS,
    ]


def with_nonce(url: str, nonce: str) -> str:
    """把 nonce 掛到 URL 的 query 上（URL 本來就可能已經有 query / fragment）。"""
    base, sep, frag = url.partition("#")
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}{NONCE_PARAM}={nonce}{sep}{frag}"


# ── Win32 ───────────────────────────────────────────────────────────────────
if IS_WIN:  # pragma: no cover - 需要真的 Windows 才跑得到
    import ctypes
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)

    _u32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    _u32.SetParent.restype = wintypes.HWND
    _u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.GetWindowLongPtrW.restype = ctypes.c_longlong
    _u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
    _u32.SetWindowLongPtrW.restype = ctypes.c_longlong
    _u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    _u32.SetWindowPos.restype = wintypes.BOOL
    _u32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_uint]
    _u32.RedrawWindow.restype = wintypes.BOOL
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.ShowWindow.restype = wintypes.BOOL
    _u32.IsWindow.argtypes = [wintypes.HWND]
    _u32.IsWindow.restype = wintypes.BOOL
    _u32.IsWindowVisible.argtypes = [wintypes.HWND]
    _u32.IsWindowVisible.restype = wintypes.BOOL
    _u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetClassNameW.restype = ctypes.c_int
    _u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetWindowTextW.restype = ctypes.c_int
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.GetWindowThreadProcessId.restype = wintypes.DWORD

    _GWL_STYLE = -16
    _WS_CHILD = 0x40000000
    _WS_POPUP = 0x80000000
    _WS_CAPTION = 0x00C00000
    _WS_THICKFRAME = 0x00040000
    _WS_VISIBLE = 0x10000000

    _SWP_NOZORDER = 0x0004
    _SWP_NOACTIVATE = 0x0010
    _SWP_FRAMECHANGED = 0x0020
    _SWP_SHOWWINDOW = 0x0040

    _RDW_INVALIDATE = 0x0001
    _RDW_ERASE = 0x0004
    _RDW_ALLCHILDREN = 0x0080
    _RDW_UPDATENOW = 0x0100

    _SW_SHOW = 5

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    # HWND 在 64 位元是 8 bytes；不宣告 argtypes 的話 ctypes 會當成 c_int 送，
    # 遇到大的 handle 直接 ArgumentError。
    _u32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _u32.EnumWindows.restype = wintypes.BOOL
    _u32.EnumChildWindows.argtypes = [wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM]
    _u32.EnumChildWindows.restype = wintypes.BOOL

    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u32.GetWindowRect.restype = wintypes.BOOL
    try:   # Windows 10 1607+ 才有；沒有就走 96 DPI 的估計值
        _u32.GetDpiForWindow.argtypes = [wintypes.HWND]
        _u32.GetDpiForWindow.restype = wintypes.UINT
    except AttributeError:  # pragma: no cover
        pass

    def _rect(hwnd: int) -> tuple[int, int, int, int]:
        r = wintypes.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def _child_windows(parent: int) -> list[tuple[int, str]]:
        """(hwnd, class) 的**遞迴**子視窗清單。Chromium 的實際畫布藏在
        `Chrome_WidgetWin_1 → Intermediate D3D Window → Chrome_RenderWidgetHostHWND`
        底下兩層，只掃一層是找不到的。"""
        out: list[tuple[int, str]] = []

        def walk(h):
            def cb(child, _lp):
                cls = ctypes.create_unicode_buffer(256)
                _u32.GetClassNameW(child, cls, 256)
                out.append((int(child), cls.value))
                walk(child)
                return True
            _u32.EnumChildWindows(h, _WNDENUMPROC(cb), 0)

        walk(parent)
        return out

    _k32 = ctypes.WinDLL("kernel32")
    _u32.SetFocus.argtypes = [wintypes.HWND]
    _u32.SetFocus.restype = wintypes.HWND
    _u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _u32.AttachThreadInput.restype = wintypes.BOOL

    def _attach_input(hwnd: int) -> int:
        """把本執行緒與 hwnd 所屬執行緒的輸入佇列接起來，回傳對方的 tid。

        **要一直接著，不能設完焦點就拆。** `AttachThreadInput(..., False)` 會把
        共用的輸入狀態拆掉，焦點歸屬也跟著失效——上一版就是設完 `SetFocus` 立刻在
        `finally` 裡拆掉，等於白設。宿主視窗要一直代管子視窗的輸入，兩邊的佇列就
        得在整個內嵌期間保持相連。
        """
        me = _k32.GetCurrentThreadId()
        tid = _u32.GetWindowThreadProcessId(hwnd, None)
        if tid and tid != me:
            _u32.AttachThreadInput(me, tid, True)
            return int(tid)
        return 0

    def _detach_input(tid: int) -> None:
        if tid:
            _u32.AttachThreadInput(_k32.GetCurrentThreadId(), tid, False)

    def _focus_across_processes(hwnd: int) -> None:
        """把鍵盤焦點交給 hwnd（佇列必須已經接好，見 `_attach_input`）。"""
        _u32.SetFocus(hwnd)

    _u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _u32.GetAncestor.restype = wintypes.HWND
    # restype 一定要宣告：不宣告的話 ctypes 當成 32 位元 c_int 收，64 位元的 HWND
    # 會被截斷／變號。這道守門是負責任的（比對錯就會去搶別的程式的鍵盤），
    # 不能靠「handle 通常夠小所以剛好沒事」。
    _u32.GetForegroundWindow.restype = wintypes.HWND

    def app_is_foreground(child_hwnd: int) -> bool:
        """`child_hwnd` 所屬的最上層視窗，現在是不是前景視窗。

        鍵盤橋接要用它當守門：只有「我們的 App 就在最前面」時才把焦點搶回 tk，
        否則使用者切到別的程式時我們會硬把焦點拉回來。
        """
        try:
            top = _u32.GetAncestor(child_hwnd, 2)     # GA_ROOT
            return bool(top) and int(top) == int(_u32.GetForegroundWindow())
        except Exception:  # noqa: BLE001
            return False

    def _is_window(hwnd: int) -> bool:
        return bool(_u32.IsWindow(hwnd))

    def _window_class(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        _u32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _window_pid(hwnd: int) -> int:
        """視窗屬於哪個行程。"""
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _enum_windows() -> list[tuple[int, int, str, str]]:
        """(hwnd, pid, class, title) 的可見頂層視窗清單。"""
        out: list[tuple[int, int, str, str]] = []

        def cb(hwnd, _lp):
            if not _u32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cls = ctypes.create_unicode_buffer(256)
            _u32.GetClassNameW(hwnd, cls, 256)
            title = ctypes.create_unicode_buffer(512)
            _u32.GetWindowTextW(hwnd, title, 512)
            out.append((int(hwnd), int(pid.value), cls.value, title.value))
            return True

        _u32.EnumWindows(_WNDENUMPROC(cb), 0)
        return out
else:  # 非 Windows：讓模組仍可 import（CI 在 Linux 上跑純邏輯測試）
    def _enum_windows() -> list[tuple[int, int, str, str]]:  # type: ignore[misc]
        return []

    def _child_windows(parent: int) -> list[tuple[int, str]]:  # type: ignore[misc]
        return []

    def _window_pid(hwnd: int) -> int:  # type: ignore[misc]
        return 0

    def _focus_across_processes(hwnd: int) -> None:  # type: ignore[misc]
        return None

    def _attach_input(hwnd: int) -> int:  # type: ignore[misc]
        return 0

    def _detach_input(tid: int) -> None:  # type: ignore[misc]
        return None

    def _is_window(hwnd: int) -> bool:  # type: ignore[misc]
        return False

    def app_is_foreground(child_hwnd: int) -> bool:  # type: ignore[misc]
        return False

    def _window_class(hwnd: int) -> str:  # type: ignore[misc]
        return ""

    def _rect(hwnd: int) -> tuple[int, int, int, int]:  # type: ignore[misc]
        return (0, 0, 0, 0)


# Chromium 在 `--app` 模式下**自己畫標題列**（含最小化／關閉鈕），那不是 Win32 的
# 非工作區，所以拿掉 WS_CAPTION 也去不掉它。內嵌時那條標題列既多餘又危險
# （使用者按下關閉鈕就會把嵌進來的視窗關掉，右欄變成一塊空白）。
# 對策：把子視窗往上挪 inset px、高度補回 inset——子視窗被父 frame 裁切，
# 標題列就被裁到看不見的地方去了。
# 啟動器行程退場後，還要再等多久視窗才算真的不會出現。
# 代價：真正的啟動失敗（執行檔壞掉之類）也要等完這段才回報，而不是像以前那樣瞬間失敗。
# 這是刻意的取捨：這段期間 UI 不會凍（`pump` 持續跑）、狀態列還寫著「正在開啟…」，
# 而誤判 handoff 的代價是使用者永遠偵不到內嵌、還多一個孤兒視窗。瀏覽器 handoff
# （stub 生出 browser process 後自己結束）通常一兩秒內就冒出視窗。
_HANDOFF_GRACE = 6.0

RENDER_WIDGET_CLASS = "Chrome_RenderWidgetHostHWND"
FALLBACK_INSET_96DPI = 33      # 量不到時的退路，之後再按 DPI 縮放


def _kill_tree(pid: int) -> None:
    """收掉一整棵行程樹。Chromium 是多行程的，只殺一個會留下一票孤兒。"""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=15)
    except Exception:  # noqa: BLE001 — 收尾不該再拋例外
        pass


def content_inset(parent_rect, widget_rect) -> int:
    """畫布上緣相對於視窗上緣的距離＝要裁掉的高度。純函式，方便測。"""
    if not widget_rect:
        return 0
    return max(0, widget_rect[1] - parent_rect[1])


def pick_window(windows, pid: int, nonce: str) -> int | None:
    """從視窗清單挑出屬於我們那個瀏覽器的視窗。

    **先認 pid、再認 nonce**——反過來會像第一版 spike 一樣抓到別人的殘留視窗。
    純函式，方便在任何平台上測。
    """
    chromium = [w for w in windows if w[2].startswith("Chrome_WidgetWin")]
    for hwnd, wpid, _cls, _title in chromium:
        if wpid == pid:
            return hwnd
    if nonce:
        for hwnd, _wpid, _cls, title in chromium:
            if nonce in title:
                return hwnd
    return None


class EmbeddedBrowser:
    """一個被塞進 tk frame 裡的 Chromium 視窗。

    生命週期：`attach()` → 需要時 `fit()` → `close()`。任何一步失敗都不拋到
    呼叫端外面（回 False / 記在 `self.error`），因為它只是體驗升級，
    壞掉時該退回開瀏覽器分頁而不是讓 App 掛掉。
    """

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.hwnd: int | None = None
        self.profile_dir: str | None = None
        self.error = ""
        self.inset = 0          # 要往上裁掉的 Chromium 自繪標題列高度
        # 這次啟動的識別碼。**收尾一定要靠它**：我們 Popen 的那個行程不一定是最後
        # 擁有視窗的那個（瀏覽器會 handoff），只認 pid 會收不乾淨。
        self.nonce = ""
        self._input_tid = 0     # 已接起來的輸入佇列（見 _attach_input）

    # -- 建立 ---------------------------------------------------------------
    def attach(self, parent_hwnd: int, url: str, *, width: int, height: int,
               timeout: float = 25.0, pump=None) -> bool:
        """`pump` 每輪輪詢呼叫一次（tk 呼叫端傳 `root.update`）。

        沒有它的話，等瀏覽器視窗出現的這一兩秒會整個凍住 UI，看起來像 App 當掉。
        """
        ok, why = available()
        if not ok:
            self.error = why
            return False
        browser = browser_path()
        nonce = self.nonce = uuid.uuid4().hex[:12]
        self.profile_dir = os.path.join(tempfile.gettempdir(),
                                        f"codexautoai-embed-{nonce}")
        argv = build_argv(browser, with_nonce(url, nonce), self.profile_dir,
                          size=(max(width, 400), max(height, 300)))
        try:
            self.proc = subprocess.Popen(argv)
        except Exception as exc:  # noqa: BLE001
            self.error = f"啟動瀏覽器外殼失敗：{exc}"
            return False

        started = time.time()
        deadline = started + timeout
        hwnd = None
        while time.time() < deadline:
            time.sleep(0.25)
            if pump is not None:
                try:
                    pump()
                except Exception:  # noqa: BLE001 — UI 已被關掉之類，不該影響內嵌流程
                    pass
            # **pump() 會把控制權交回 UI**，使用者可能就在這一刻按了「收合」或關掉
            # App，那條路徑會呼叫 close() 把 self.proc 設成 None。所以每輪都要重新
            # 取一次快照再用——直接 `self.proc.poll()` 會炸成 AttributeError。
            proc = self.proc
            if proc is None:
                self.error = "開啟途中被取消"
                self.close()
                return False
            # 行程還活著才信得過它的 pid。已經退場的話那個 pid 隨時會被 Windows
            # 配給別人，拿它去比對可能挑中**不相干的 Chromium 視窗**，然後把別人
            # 的瀏覽器 SetParent 進我們的欄位裡。死了就只認 nonce。
            proc_pid = proc.pid if proc.poll() is None else -1
            hwnd = pick_window(_enum_windows(), proc_pid, nonce)
            if hwnd:
                break
            # **啟動器行程先退場是正常的**（瀏覽器 handoff：stub 生出真正的 browser
            # process 之後自己結束），視窗照樣會出現，只是屬於別的 pid——nonce 那條
            # 退路就是為了這種情況存在的。原本一看到 poll() 有值就立刻放棄，等於
            # 在 0.3 秒內丟下一個已經啟動、之後才會冒出視窗的瀏覽器不管：
            # 呼叫端接著開 fallback 視窗，使用者就同時看到兩個視窗（實測會殘留
            # 25 個 msedge 行程）。所以只有「行程死了**而且**寬限期內也沒等到
            # 視窗」才算真的失敗。
            if proc.poll() is not None and time.time() - started > _HANDOFF_GRACE:
                self.error = "瀏覽器外殼結束了，也沒有出現它的視窗"
                self.close()
                return False
        if not hwnd:
            self.error = "找不到瀏覽器外殼的視窗（逾時）"
            self.close()
            return False

        if self.proc is None:          # 找到視窗的同一輪被取消掉了
            self.error = "開啟途中被取消"
            return False
        try:
            self._reparent(hwnd, parent_hwnd)
        except Exception as exc:  # noqa: BLE001
            self.error = f"內嵌視窗失敗：{exc}"
            self.close()
            return False
        self.hwnd = hwnd
        # 內嵌期間一直讓兩邊的輸入佇列相連，鍵盤才進得來
        self._input_tid = _attach_input(hwnd)
        self._measure_inset(pump)
        # `_measure_inset` 還會再 pump 最多 6 秒——同一條取消路徑在這裡一樣到得了，
        # 而且瀏覽器也可能自己掛掉。**不重新確認就 return True** 的話，呼叫端會
        # 拿到一個殭屍實例、狀態列還綠字寫「已嵌在右邊欄位」，右欄卻是一片空白，
        # 要等下一次 resize 事件才自我修正。
        if not self.alive or not self.hwnd:
            self.error = self.error or "開啟途中被取消"
            self.close()
            return False
        self.fit(width, height)
        return True

    def _measure_inset(self, pump=None, timeout: float = 6.0) -> None:
        """量出要裁掉的標題列高度。

        畫布（`Chrome_RenderWidgetHostHWND`）要等頁面真的開始 render 才出現，
        所以這裡輪詢等它；等不到就用按 DPI 縮放的估計值，寧可裁掉一點內容也
        不要把「關閉鈕」留在畫面上給使用者誤按。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            # 同上：這裡也會 pump，self.hwnd 可能被 close() 清掉。用 None 去
            # EnumChildWindows 會變成列舉整個桌面的子視窗，不是我們要的。
            if not self.hwnd:
                return
            widget = next((h for h, cls in _child_windows(self.hwnd)
                           if cls.startswith(RENDER_WIDGET_CLASS)), None)
            if widget:
                inset = content_inset(_rect(self.hwnd), _rect(widget))
                if inset > 0:
                    self.inset = inset
                    return
            time.sleep(0.2)
            if pump is not None:
                try:
                    pump()
                except Exception:  # noqa: BLE001
                    pass
        try:
            dpi = _u32.GetDpiForWindow(self.hwnd) or 96
        except Exception:  # noqa: BLE001
            dpi = 96
        self.inset = round(FALLBACK_INSET_96DPI * dpi / 96)

    def _reparent(self, hwnd: int, parent_hwnd: int) -> None:
        """先把 style 改成 child 再 SetParent——直接 SetParent 而不改 style 會留下
        標題列與 resize 邊框，看起來就是「視窗裡卡了一個視窗」。"""
        style = _u32.GetWindowLongPtrW(hwnd, _GWL_STYLE)
        style = (style & ~_WS_POPUP & ~_WS_CAPTION & ~_WS_THICKFRAME) \
            | _WS_CHILD | _WS_VISIBLE
        _u32.SetWindowLongPtrW(hwnd, _GWL_STYLE, style)
        _u32.SetParent(hwnd, parent_hwnd)
        _u32.ShowWindow(hwnd, _SW_SHOW)

    # -- 尺寸 ---------------------------------------------------------------
    def fit(self, width: int, height: int) -> None:
        """跟著父 frame 調尺寸。**一定要補重畫**——見模組 docstring 的坑 1。"""
        if not self.hwnd or not self.alive:
            return
        try:
            _u32.SetWindowPos(self.hwnd, 0, 0, -self.inset,
                              max(width, 1), max(height, 1) + self.inset,
                              _SWP_NOZORDER | _SWP_NOACTIVATE
                              | _SWP_FRAMECHANGED | _SWP_SHOWWINDOW)
            _u32.RedrawWindow(self.hwnd, None, None,
                              _RDW_INVALIDATE | _RDW_ERASE
                              | _RDW_ALLCHILDREN | _RDW_UPDATENOW)
        except Exception:  # noqa: BLE001
            pass

    def focus(self) -> None:
        """把鍵盤焦點交給嵌進來的瀏覽器。

        **reparent 進別的行程之後鍵盤不會自己過來**：tk 的 toplevel 才是前景視窗，
        按鍵預設進 tk 的佇列。使用者點了終端機卻打不了字就是這個原因。

        焦點要下在**畫布**（`Chrome_RenderWidgetHostHWND`）而不是外框——外框拿到
        焦點時 Chromium 不一定會把它轉交給 renderer。
        """
        if not self.hwnd:
            return
        try:
            target = next((h for h, cls in _child_windows(self.hwnd)
                           if cls.startswith(RENDER_WIDGET_CLASS)), self.hwnd)
            _focus_across_processes(target)
        except Exception:  # noqa: BLE001 — 對不到焦點不該讓 App 出錯
            pass

    # -- 收尾 ---------------------------------------------------------------
    @property
    def alive(self) -> bool:
        """還活著嗎——**以視窗為準，不是我們 Popen 的那個行程**。

        handoff 的定義就是「啟動器 stub 生出真正的 browser process 之後自己退場」，
        所以拿 stub 的存活狀態當判準，在 handoff 情境下必然是 False：視窗明明已經
        找到、也 reparent 進來了，`attach()` 最後那道存活確認還是會把它砍掉、回傳
        False，呼叫端照樣去開 fallback 視窗——正是這次要修的症狀。

        已經有 hwnd 就看視窗還在不在；還沒拿到 hwnd（正在等視窗出現）才看行程。
        """
        if self.hwnd:
            if not IS_WIN:
                return True
            try:
                return bool(_u32.IsWindow(self.hwnd))
            except Exception:  # noqa: BLE001
                return True
        return self.proc is not None and self.proc.poll() is None

    def close(self) -> None:
        """殺掉整棵行程樹並刪掉暫時 profile。

        Chromium 是多行程的（browser + gpu + 每個 renderer），只 `terminate()`
        父行程會留下一票孤兒，所以走 `taskkill /T`。

        **光殺我們 Popen 的那個 pid 不夠**：瀏覽器會 handoff，最後擁有視窗的
        很可能是別的行程，而它一旦不在我們的行程樹裡，`/T` 就掃不到——留下一個
        沒人管的視窗浮在桌面上（實測殘留 25 個 msedge 行程）。所以再用 nonce
        掃一次視窗，把真正擁有它的行程一起收掉。
        """
        _detach_input(self._input_tid)
        self._input_tid = 0
        proc, self.proc = self.proc, None
        hwnd, self.hwnd = self.hwnd, None
        pids = []
        if proc is not None and proc.poll() is None:
            pids.append(proc.pid)
        # **當下**去問視窗的擁有者，不要用 attach 當時記下來的 pid：那個行程若已經
        # 結束，Windows 很快就會把同一個 pid 配給別人，照著殺會誤傷不相干的行程樹。
        # 先確認視窗還在，再問它現在屬於誰。
        # HWND 也會被回收（比 pid 罕見，要 handle table 繞一圈才會撞上），所以
        # 除了確認視窗還在，再確認它**還是個 Chromium 視窗**才動手。
        if hwnd and _is_window(hwnd) and _window_class(hwnd).startswith("Chrome_WidgetWin"):
            owner = _window_pid(hwnd)
            if owner:
                pids.append(owner)
        pids.extend(self._orphan_pids())
        # 去重：handoff 之後 popen 的 pid 與視窗擁有者可能是同一個，`taskkill` 每次
        # 最長會擋 15 秒且是**同步跑在呼叫端執行緒**上（收合面板就是 UI 執行緒），
        # 重複收同一棵樹等於把最壞情況乘二。`_kill_tree` 已經涵蓋整棵樹，收完就好，
        # 不必再補一次 `proc.kill()`。
        for pid in dict.fromkeys(pids):
            _kill_tree(pid)
        self.nonce = ""
        if self.profile_dir:
            shutil.rmtree(self.profile_dir, ignore_errors=True)
            self.profile_dir = None

    def _orphan_pids(self) -> list[int]:
        """還掛著我們 nonce 的視窗，各自屬於哪個行程。"""
        if not self.nonce:
            return []
        try:
            return [pid for _h, pid, cls, title in _enum_windows()
                    if cls.startswith("Chrome_WidgetWin") and self.nonce in title]
        except Exception:  # noqa: BLE001 — 收尾不該再拋例外
            return []


if __name__ == "__main__":  # 手動檢查用：python desktop/winembed.py
    ok, why = available()
    print(f"可內嵌：{ok} {why}")
    print(f"瀏覽器：{browser_path()}")
    sys.exit(0 if ok else 1)
