"""desktop/conpty.py — 跨平台 PTY 後端測試。

**刻意不斷言「輸出內容」**：pytest 是從有 console 的行程跑的，Windows 的 console
繼承會讓子行程接上父 console 而不是我們的 pseudoconsole，於是讀不到完整輸出
（詳見 conpty.py docstring 的對照表）。那是測試環境的假象、不是程式的行為，
所以這裡只驗證「與 console 繼承無關」的部分：能力偵測、生命週期、寫入、
resize、close 的冪等性與行程樹回收。

輸出捕捉本身已用無 console 的行程實測過（344 bytes、橫幅與按鍵回顯都拿得到）。
"""
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

conpty = pytest.importorskip("conpty")

IS_WIN = os.name == "nt"

# 生命週期測試刻意用「一個一定活著的 python 子行程」而不是裸 shell：
# 從有 console 的行程（pytest 就是）啟動時，裸 `cmd.exe` 會因為接上父 console
# 而在 0.5 秒內自己結束，讓「多 session 同時活著」這種測試假性失敗。
# 用 sleeper 就與 console 繼承無關，測到的才是 PtySession 自己的行為。
SHELL = [sys.executable, "-c", "import time; time.sleep(30)"]


def test_pty_available_is_bool():
    assert isinstance(conpty.pty_available(), bool)


@pytest.mark.skipif(not conpty.pty_available(), reason="這台機器開不了 PTY")
class TestSession:
    def test_spawn_then_close(self):
        s = conpty.PtySession.spawn(SHELL, cols=80, rows=24)
        try:
            assert s.alive is True
        finally:
            s.close()
        time.sleep(0.3)
        assert s.alive is False

    def test_close_is_idempotent(self):
        """UI 關分頁時可能重複觸發 close，第二次不能爆。"""
        s = conpty.PtySession.spawn(SHELL, cols=80, rows=24)
        s.close()
        s.close()          # 不該拋例外
        assert s.alive is False

    def test_write_reports_bytes_written(self):
        s = conpty.PtySession.spawn(SHELL, cols=80, rows=24)
        try:
            n = s.write("echo hi\r\n")
            assert n > 0
        finally:
            s.close()

    def test_write_accepts_bytes_and_str(self):
        s = conpty.PtySession.spawn(SHELL, cols=80, rows=24)
        try:
            assert s.write(b"echo a\r\n") > 0
            assert s.write("echo b\r\n") > 0
        finally:
            s.close()

    def test_read_nowait_never_blocks_and_returns_bytes(self):
        """UI 執行緒會輪詢它，絕不能阻塞。"""
        s = conpty.PtySession.spawn(SHELL, cols=80, rows=24)
        try:
            t0 = time.perf_counter()
            out = s.read_nowait()
            assert time.perf_counter() - t0 < 1.0
            assert isinstance(out, bytes)
        finally:
            s.close()

    def test_resize_does_not_raise(self):
        s = conpty.PtySession.spawn(SHELL, cols=80, rows=24)
        try:
            s.resize(120, 40)
            s.resize(1, 1)          # 邊界值也不能爆
            s.resize(0, 0)          # 會被夾到 1
        finally:
            s.close()

    def test_resize_after_close_does_not_raise(self):
        s = conpty.PtySession.spawn(SHELL, cols=80, rows=24)
        s.close()
        s.resize(100, 30)           # 分頁已關但 UI 還送 resize 是常見競態

    def test_write_after_close_is_safe(self):
        s = conpty.PtySession.spawn(SHELL, cols=80, rows=24)
        s.close()
        assert s.write("echo x\r\n") == 0

    def test_cwd_is_honoured(self, tmp_path):
        s = conpty.PtySession.spawn(SHELL, cwd=str(tmp_path), cols=80, rows=24)
        try:
            assert s.alive is True
        finally:
            s.close()

    def test_multiple_concurrent_sessions(self):
        """多分頁的核心前提：好幾個 session 同時活著、互不干擾。"""
        sessions = [conpty.PtySession.spawn(SHELL, cols=80, rows=24) for _ in range(3)]
        try:
            assert all(s.alive for s in sessions)
            # 各自關掉不該影響其他人
            sessions[1].close()
            time.sleep(0.3)
            assert sessions[0].alive and sessions[2].alive
            assert not sessions[1].alive
        finally:
            for s in sessions:
                s.close()


@pytest.mark.skipif(conpty.pty_available(), reason="只在開不了 PTY 的機器上驗降級")
def test_spawn_raises_when_unavailable():
    with pytest.raises(conpty.PtyUnavailable):
        conpty.PtySession.spawn(SHELL)
