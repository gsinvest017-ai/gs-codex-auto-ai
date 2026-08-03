#!/usr/bin/env python3
"""
conpty.py — 跨平台 PTY（pseudo-terminal）後端，**純標準庫**。

為什麼要有這個：desktop App 原本每開一個 Claude / Codex session 就 `Popen` 一個
**外部原生終端機視窗**，session 一多就散在桌面各處、關掉 App 也不會收乾淨，
而且 App 完全看不到裡面的輸出。要把 session 內嵌進 App 並用分頁管理，就需要
自己持有 pty，才讀得到輸出、送得進按鍵、改得了視窗大小。

為什麼不用 pywinpty：`desktop/` 有純標準庫不變式（見 CLAUDE.md）——本 App 會被
PyInstaller 凍結、塞進 .vsix、丟進使用者的任意專案直接跑。pywinpty 是需要 Rust
才能從源碼編的編譯型套件；而 ConPTY 本身只是幾支有文件的 Win32 API，用 `ctypes`
就能呼叫。實測（spike）確認可行：能開 pseudoconsole、跑互動式行程、讀到帶 ANSI
的輸出、寫得進 stdin。

  * Windows 10 1809+ / 11 → ConPTY（`CreatePseudoConsole` 等）
  * POSIX                 → 標準庫 `pty` + `os.forkpty`

實作依 Microsoft 公開文件寫成，未參考任何 GPL 實作（PyConPTY 為 GPLv3，本 repo
是 public 且公開發行，不能引入其程式碼）。

## 從終端機測試時會看到的假象（**不要當成 bug 去修**）

如果你在一個「有 console」的行程裡（例如從 PowerShell / bash 直接跑 python）測本模組，
會看到兩個看起來很像壞掉的現象：

  * 子行程的橫幅／提示字元**跑到你的終端機上**，而不是被 pty 收走；
  * `read_nowait()` 只拿得到 16 bytes（ConPTY 自己發的 `\\x1b[?9001h\\x1b[?1004h`），
    互動式程式（如裸的 `cmd.exe`）甚至可能很快就結束。

原因是 **console 繼承**：Windows 的 console 不受 `bInheritHandles=False` 管，子行程
會接上父行程既有的 console，於是根本沒去用我們給它的 pseudoconsole。

實測對照（同一份程式碼）：

  | 父行程 | 讀到 | 橫幅被捕捉 | 按鍵回顯 |
  |---|---|---|---|
  | 從終端機啟動（繼承 console） | 16 B | ✗ | ✗ |
  | `CREATE_NO_WINDOW` 啟動（無 console） | 344 B | ✓ | ✓ |

桌面 App 是 `console=False` 的凍結執行檔（見 `CodexAutoAI.spec`），本來就沒有 console
可繼承，所以走的是下面那列。**要驗證本模組請用沒有 console 的行程跑**，
不要從終端機直接跑然後以為它壞了。

另外實測確認：`DETACHED_PROCESS` 旗標會讓 ConPTY 完全收不到輸出（0 bytes），
不要為了「避免視窗」加它。

用法：

    s = PtySession.spawn(["claude"], cwd="C:/proj", cols=100, rows=30)
    s.write("你好\\r")
    data = s.read_nowait()      # bytes，沒有就回 b""
    s.resize(120, 40)
    s.close()
"""
from __future__ import annotations

import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
from typing import Optional

IS_WIN = os.name == "nt"

__all__ = ["PtySession", "pty_available", "PtyUnavailable"]


class PtyUnavailable(RuntimeError):
    """這台機器開不了 PTY（太舊的 Windows、或缺少必要 API）。"""


# ---------------------------------------------------------------------------
# Windows：ConPTY via ctypes
# ---------------------------------------------------------------------------
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    HPCON = wintypes.HANDLE
    _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000

    class _COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", _STARTUPINFOW),
                    ("lpAttributeList", ctypes.c_void_p)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    def _bind() -> None:
        _k32.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE),
                                    ctypes.POINTER(wintypes.HANDLE),
                                    ctypes.c_void_p, wintypes.DWORD]
        _k32.CreatePipe.restype = wintypes.BOOL
        _k32.CreatePseudoConsole.argtypes = [_COORD, wintypes.HANDLE, wintypes.HANDLE,
                                             wintypes.DWORD, ctypes.POINTER(HPCON)]
        _k32.CreatePseudoConsole.restype = ctypes.HRESULT
        _k32.ResizePseudoConsole.argtypes = [HPCON, _COORD]
        _k32.ResizePseudoConsole.restype = ctypes.HRESULT
        _k32.ClosePseudoConsole.argtypes = [HPCON]
        _k32.ClosePseudoConsole.restype = None
        _k32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t)]
        _k32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        _k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        _k32.DeleteProcThreadAttributeList.restype = None
        _k32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        _k32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        _k32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
            wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(_STARTUPINFOEXW), ctypes.POINTER(_PROCESS_INFORMATION)]
        _k32.CreateProcessW.restype = wintypes.BOOL
        _k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        _k32.ReadFile.restype = wintypes.BOOL
        _k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        _k32.WriteFile.restype = wintypes.BOOL
        _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.DWORD)]
        _k32.GetExitCodeProcess.restype = wintypes.BOOL

    if hasattr(_k32, "CreatePseudoConsole"):
        _bind()

    _STILL_ACTIVE = 259


def pty_available() -> bool:
    """這台機器能不能開 PTY。Windows 需 10 1809+（有 CreatePseudoConsole）。"""
    if IS_WIN:
        try:
            return hasattr(_k32, "CreatePseudoConsole")
        except Exception:  # noqa: BLE001
            return False
    try:
        import pty  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class PtySession:
    """一個跑在 pty 裡的行程。輸出由背景執行緒收進 queue，呼叫端輪詢取用。

    刻意用「背景讀取執行緒 + queue」而不是讓呼叫端自己 read：Windows 的
    `ReadFile` 對 pipe 是阻塞的，直接在 UI 執行緒讀會整個卡住。
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._closed = threading.Event()
        self._reader: Optional[threading.Thread] = None
        # Windows
        self._hpc = None
        self._pi = None
        self._in_w = None
        self._out_r = None
        self._attrbuf = None
        # POSIX
        self._fd: Optional[int] = None
        self._pid: Optional[int] = None

    # -- 建立 ---------------------------------------------------------------
    @classmethod
    def spawn(cls, argv: list[str], *, cwd: Optional[str] = None,
              env: Optional[dict] = None, cols: int = 100, rows: int = 30) -> "PtySession":
        if not pty_available():
            raise PtyUnavailable(
                "這台機器開不了 PTY（Windows 需 10 1809 以上）。")
        self = cls()
        if IS_WIN:
            self._spawn_windows(argv, cwd, env, cols, rows)
        else:
            self._spawn_posix(argv, cwd, env, cols, rows)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        return self

    # -- Windows ------------------------------------------------------------
    def _spawn_windows(self, argv, cwd, env, cols, rows) -> None:
        import ctypes
        from ctypes import wintypes

        def check(ok, what):
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error(), f"{what} 失敗")

        in_r, in_w = wintypes.HANDLE(), wintypes.HANDLE()
        out_r, out_w = wintypes.HANDLE(), wintypes.HANDLE()
        check(_k32.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w), None, 0), "CreatePipe(in)")
        check(_k32.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w), None, 0), "CreatePipe(out)")

        hpc = HPCON()
        hr = _k32.CreatePseudoConsole(_COORD(cols, rows), in_r, out_w, 0, ctypes.byref(hpc))
        if hr != 0:
            raise PtyUnavailable(f"CreatePseudoConsole 失敗 HRESULT=0x{hr & 0xFFFFFFFF:08X}")

        size = ctypes.c_size_t(0)
        _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attrbuf = ctypes.create_string_buffer(size.value)
        si = _STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        si.lpAttributeList = ctypes.cast(attrbuf, ctypes.c_void_p)
        check(_k32.InitializeProcThreadAttributeList(
            si.lpAttributeList, 1, 0, ctypes.byref(size)), "InitializeProcThreadAttributeList")
        check(_k32.UpdateProcThreadAttribute(
            si.lpAttributeList, 0, _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            ctypes.cast(hpc, ctypes.c_void_p), ctypes.sizeof(HPCON), None, None),
            "UpdateProcThreadAttribute")

        pi = _PROCESS_INFORMATION()
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        envblock = _env_block(env) if env else None
        flags = _EXTENDED_STARTUPINFO_PRESENT | (0x00000400 if envblock else 0)  # CREATE_UNICODE_ENVIRONMENT
        check(_k32.CreateProcessW(None, cmdline, None, None, False, flags,
                                  envblock, cwd, ctypes.byref(si), ctypes.byref(pi)),
              "CreateProcessW")

        # 子行程已繼承這兩端，父行程必須關掉自己的複本，否則 ReadFile 永遠等不到 EOF。
        _k32.CloseHandle(out_w)
        _k32.CloseHandle(in_r)

        self._hpc, self._pi, self._in_w, self._out_r = hpc, pi, in_w, out_r
        self._attrbuf = attrbuf   # 保住引用，別讓 GC 回收掉 attribute list

    # -- POSIX --------------------------------------------------------------
    def _spawn_posix(self, argv, cwd, env, cols, rows) -> None:
        import fcntl
        import pty
        import struct
        import termios

        pid, fd = pty.fork()
        if pid == 0:                                   # child
            try:
                if cwd:
                    os.chdir(cwd)
                os.execvpe(argv[0], argv, env or os.environ)
            except Exception:                          # noqa: BLE001
                os._exit(127)
        fcntl.ioctl(fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))
        os.set_blocking(fd, False)
        self._fd, self._pid = fd, pid

    # -- 讀取迴圈 -----------------------------------------------------------
    def _read_loop(self) -> None:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_string_buffer(8192)
            n = wintypes.DWORD(0)
            while not self._closed.is_set():
                ok = _k32.ReadFile(self._out_r, buf, 8192, ctypes.byref(n), None)
                if not ok or n.value == 0:
                    break
                self._q.put(buf.raw[:n.value])
        else:
            import select
            while not self._closed.is_set():
                try:
                    r, _, _ = select.select([self._fd], [], [], 0.2)
                    if not r:
                        continue
                    chunk = os.read(self._fd, 8192)
                    if not chunk:
                        break
                    self._q.put(chunk)
                except (OSError, ValueError):
                    break
        self._closed.set()

    # -- 公開 API -----------------------------------------------------------
    def read_nowait(self, max_chunks: int = 64) -> bytes:
        """取出目前已緩衝的輸出；沒有就回 `b""`（不阻塞）。"""
        out = []
        for _ in range(max_chunks):
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return b"".join(out)

    def write(self, data: str | bytes) -> int:
        """把按鍵送進 pty。"""
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            n = wintypes.DWORD(0)
            if not _k32.WriteFile(self._in_w, data, len(data), ctypes.byref(n), None):
                return 0
            return n.value
        try:
            return os.write(self._fd, data)
        except OSError:
            return 0

    def resize(self, cols: int, rows: int) -> None:
        """視窗大小變了要同步告訴 pty，否則 TUI（含 claude）換行會亂掉。"""
        cols, rows = max(1, int(cols)), max(1, int(rows))
        if IS_WIN:
            try:
                _k32.ResizePseudoConsole(self._hpc, _COORD(cols, rows))
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            import fcntl
            import struct
            import termios
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:  # noqa: BLE001
            pass

    @property
    def alive(self) -> bool:
        if IS_WIN:
            if self._pi is None:
                return False
            import ctypes
            from ctypes import wintypes
            code = wintypes.DWORD(0)
            if not _k32.GetExitCodeProcess(self._pi.hProcess, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        if self._pid is None:
            return False
        try:
            pid, _ = os.waitpid(self._pid, os.WNOHANG)
            return pid == 0
        except ChildProcessError:
            return False
        except OSError:
            return False

    def close(self) -> None:
        """關閉 pty 並確保子行程樹被收掉（不留孤兒視窗／行程）。"""
        if self._closed.is_set() and self._hpc is None and self._fd is None:
            return
        self._closed.set()
        if IS_WIN:
            try:
                if self._pi is not None:
                    # 殺整棵樹：claude 底下還有 node，只殺頂層會留孤兒。
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._pi.dwProcessId)],
                                   capture_output=True, timeout=15)
            except Exception:  # noqa: BLE001
                pass
            for h in (self._in_w, self._out_r):
                try:
                    if h:
                        _k32.CloseHandle(h)
                except Exception:  # noqa: BLE001
                    pass
            try:
                if self._hpc is not None:
                    _k32.ClosePseudoConsole(self._hpc)
            except Exception:  # noqa: BLE001
                pass
            self._hpc = self._in_w = self._out_r = None
            self._pi = None
        else:
            try:
                if self._pid:
                    os.killpg(os.getpgid(self._pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                try:
                    if self._pid:
                        os.kill(self._pid, signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    pass
            try:
                if self._fd is not None:
                    os.close(self._fd)
            except Exception:  # noqa: BLE001
                pass
            self._fd = self._pid = None


def _env_block(env: dict):
    """把 dict 轉成 CreateProcessW 要的 unicode environment block。"""
    import ctypes
    items = "".join(f"{k}={v}\0" for k, v in env.items()) + "\0"
    return ctypes.cast(ctypes.create_unicode_buffer(items), ctypes.c_void_p)


def which(name: str) -> Optional[str]:
    """找可執行檔（Windows 上 claude/codex 是 .cmd 蓋子，shutil.which 找得到）。"""
    return shutil.which(name)


if __name__ == "__main__":   # 手動煙霧測試：python desktop/conpty.py
    import time
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print("pty_available:", pty_available())
    shell = ["cmd.exe"] if IS_WIN else ["/bin/sh"]
    s = PtySession.spawn(shell, cols=80, rows=24)
    s.write("echo hello-from-pty\r\n" if IS_WIN else "echo hello-from-pty\n")
    time.sleep(1.5)
    print(s.read_nowait().decode("utf-8", "replace")[:400])
    print("alive:", s.alive)
    s.close()
    print("closed; alive:", s.alive)
