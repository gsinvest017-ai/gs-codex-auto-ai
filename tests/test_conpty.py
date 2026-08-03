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


# ── 強制結束時不留孤兒（job object）──────────────────────────────────────────
@pytest.mark.skipif(not IS_WIN, reason="job object 是 Windows 專屬機制")
@pytest.mark.skipif(not conpty.pty_available(), reason="這台機器開不了 PTY")
def test_detached_grandchild_dies_when_owner_is_force_killed(tmp_path):
    """App 被**強制結束**時，連「脫離的孫行程」也要被收掉。

    測的是孫行程而不是直屬子行程，因為**只有孫行程這層會有差**：直屬 pty 子行程
    由 ConPTY / conhost 自己處理，owner 一死它就跟著死，有沒有 job 都一樣。
    但 claude 底下還會再生 node，那類行程若以 DETACHED_PROCESS 起來就不受 conhost
    管；實測（拿掉 job 綁定）確認它會活下來變成看不見的孤兒，繼續佔資源與額度。
    KILL_ON_JOB_CLOSE 的 job object 是這一層的保證。

    情境是真的會遇到的：安裝新版時 Inno Setup 會關掉執行中的 App，逾時就改用
    TerminateProcess——那時 `close()` 裡的 taskkill 根本沒機會執行。
    """
    import subprocess
    import textwrap
    import time

    gpid = tmp_path / "grandchild.pid"
    # pty 子行程再生一個 DETACHED 的孫行程，模擬 claude → node
    inner = (
        "import subprocess,sys,time,pathlib;"
        "g=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],"
        "creationflags=0x00000008|0x00000200);"
        f"pathlib.Path(r'{gpid}').write_text(str(g.pid));"
        "time.sleep(120)"
    )
    helper = tmp_path / "helper.py"
    helper.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(ROOT / "desktop")!r})
        import conpty
        s = conpty.PtySession.spawn([sys.executable, "-c", {inner!r}], cols=80, rows=24)
        time.sleep(60)
    """), encoding="utf-8")

    owner = subprocess.Popen([sys.executable, str(helper)],
                             creationflags=0x08000000)   # CREATE_NO_WINDOW
    grandchild = None
    try:
        for _ in range(80):
            if gpid.exists() and gpid.read_text().strip():
                break
            time.sleep(0.25)
        assert gpid.exists() and gpid.read_text().strip(), "孫行程沒有起來，測試前提不成立"
        grandchild = int(gpid.read_text().strip())
        assert _pid_alive(grandchild), "孫行程應該先是活著的"

        owner.kill()                      # 模擬 TerminateProcess，不給清理機會
        owner.wait(timeout=15)

        for _ in range(40):               # OS 回收 job 需要一點時間
            if not _pid_alive(grandchild):
                break
            time.sleep(0.25)
        assert not _pid_alive(grandchild), "強制結束後脫離的孫行程仍存活＝孤兒行程"
    finally:
        if owner.poll() is None:
            owner.kill()
        if grandchild and _pid_alive(grandchild):
            subprocess.run(["taskkill", "/F", "/PID", str(grandchild)],
                           capture_output=True)


def _pid_alive(pid: int) -> bool:
    import subprocess
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=20)
    return str(pid) in (r.stdout or "")


# ── PATHEXT 蓋子（.cmd / .bat / .ps1）────────────────────────────────────────
class TestPathextShims:
    """`claude` / `codex` 用 npm 裝出來是 `.cmd` / `.ps1` 蓋子，不是 `.exe`。

    這是實際回報的當機（2026-08-03，另一台機器）：
    `開啟失敗：[WinError 2] CreateProcessW 失敗`。原因是可用性檢查與實際啟動
    用了**兩套不同的解析規則**——`shutil.which()` 照 PATHEXT 找得到 `codex.CMD`，
    於是 UI 說「已安裝」；但 `CreateProcessW(lpApplicationName=NULL)` 名稱沒有
    副檔名時**只補 `.exe`、不看 PATHEXT**，直接 ERROR_FILE_NOT_FOUND。

    當初沒測出來是因為開發機的 `claude` 恰好是原生 `.exe`，而 live test 只開過
    `shell` 與 `claude`，從沒開過 `codex`。
    """

    def test_resolves_to_full_path(self, monkeypatch, tmp_path):
        """裸名稱要被換成完整路徑——這正是 CreateProcessW 需要的形式。"""
        binv = tmp_path / "bin"
        binv.mkdir()
        shim = binv / ("mytool.cmd" if IS_WIN else "mytool")
        shim.write_text("@echo off\n" if IS_WIN else "#!/bin/sh\n", encoding="utf-8")
        if not IS_WIN:
            shim.chmod(0o755)
        monkeypatch.setenv("PATH", str(binv) + os.pathsep + os.environ.get("PATH", ""))
        if IS_WIN:
            monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")

        out = conpty.resolve_argv(["mytool", "--flag", "帶空白的 引數"])
        assert Path(out[0]) == shim, f"沒解析成完整路徑：{out[0]}"
        assert out[1:] == ["--flag", "帶空白的 引數"], "後面的引數不能被動到"

    def test_custom_env_path_is_used_for_resolution(self, monkeypatch, tmp_path):
        """帶了 `env` 就要用**那份** PATH 解析。

        否則會變成「拿父行程的 PATH 找、用子行程的 PATH 跑」——兩邊默默指向不同
        的執行檔，而這種不一致最難查。
        """
        theirs = tmp_path / "theirs"
        theirs.mkdir()
        shim = theirs / ("only-here.cmd" if IS_WIN else "only-here")
        shim.write_text("@echo off\n" if IS_WIN else "#!/bin/sh\n", encoding="utf-8")
        if not IS_WIN:
            shim.chmod(0o755)
        # 父行程的 PATH 裡**沒有**這個東西
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        if IS_WIN:
            monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")

        assert conpty.resolve_argv(["only-here"]) == ["only-here"], "父 PATH 不該找得到"
        out = conpty.resolve_argv(["only-here"], {"PATH": str(theirs)})
        assert Path(out[0]) == shim, f"沒有用 env 的 PATH 解析：{out[0]}"

    @pytest.mark.parametrize("key", ["PATH", "Path", "path"])
    def test_env_path_key_is_case_insensitive(self, monkeypatch, tmp_path, key):
        """Windows 的環境變數名不分大小寫，手工組的 env 寫成 `Path` 也合法。

        只認 `PATH` 的話會**默默**退回父行程的 PATH 去解析——沒有錯誤訊息，
        只是找到了另一個執行檔。
        """
        theirs = tmp_path / "theirs"
        theirs.mkdir()
        shim = theirs / ("only-here.cmd" if IS_WIN else "only-here")
        shim.write_text("@echo off\n" if IS_WIN else "#!/bin/sh\n", encoding="utf-8")
        if not IS_WIN:
            shim.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        if IS_WIN:
            monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")

        out = conpty.resolve_argv(["only-here"], {key: str(theirs)})
        assert Path(out[0]) == shim, f"env 的 {key} 沒被認出來：{out[0]}"

    def test_unknown_command_passes_through(self):
        """找不到就原樣傳回去，讓 CreateProcessW 自己報錯，不要多發明一種訊息。"""
        assert conpty.resolve_argv(["絕對不存在的指令-xyz"]) == ["絕對不存在的指令-xyz"]

    def test_empty_argv_is_safe(self):
        assert conpty.resolve_argv([]) == []

    def test_absolute_path_is_left_alone(self):
        assert conpty.resolve_argv([sys.executable])[0] == sys.executable

    @pytest.mark.skipif(not IS_WIN, reason="PATHEXT 蓋子是 Windows 專屬問題")
    @pytest.mark.skipif(not conpty.pty_available(), reason="這台機器開不了 PTY")
    def test_can_spawn_a_cmd_shim(self, monkeypatch, tmp_path):
        """真的去啟動一個 `.cmd` 蓋子——沒有解析這一層就是 WinError 2。

        只斷言「啟動得起來」，不斷言輸出：console 繼承會讓輸出跑到父 console
        （見本檔開頭說明）。而 WinError 2 是在啟動階段就炸，所以這樣就夠了。
        """
        binv = tmp_path / "bin"
        binv.mkdir()
        (binv / "fakecli.cmd").write_text(
            "@echo off\r\nping -n 30 127.0.0.1 >nul\r\n", encoding="utf-8")
        monkeypatch.setenv("PATH", str(binv) + os.pathsep + os.environ.get("PATH", ""))

        sess = conpty.PtySession.spawn(["fakecli"], cwd=str(tmp_path))
        try:
            assert sess.alive
        finally:
            sess.close()


class TestBatchArgumentInjection:
    """`.cmd` / `.bat` 目標會被 `CreateProcessW` **偷偷轉交 cmd.exe**，所以後面的
    參數是被 cmd 的文法解析的。而 session 的需求字串（prompt）就是最後一個參數，
    且它從 HTTP POST body 直接進來（`termserver._create`），完全沒有清理。

    `list2cmdline` 是 MSVCRT 規則、**只在有空白或引號時才加引號**——所以不含空白的
    `x&calc` 根本不會被引起來。實測（2026-08-03）這種 payload 真的會執行。

    > 註：第一版驗證用的 payload 全都含空白，因此全被引號保護住、驗不出問題。
    > 下面刻意用**不含空白**的形狀，那才是會咬人的那種。
    """

    def test_batch_target_quotes_every_argument(self):
        """一律加引號——cmd 在雙引號內不解讀 `& | < > ^`。"""
        line = conpty.build_cmdline([r"C:\x\codex.CMD", "x&calc"])
        assert line.endswith('"x&calc"'), line

    def test_batch_target_neutralises_quotes(self):
        """`"` 是唯一能脫出引號的字元，cmd 上沒有能安全表示它的引用方式。"""
        assert '"' not in conpty._quote_for_batch('a" & calc & "b')[1:-1]

    def test_batch_target_neutralises_percent(self):
        """`%VAR%` 展開發生在**引號之內**，加引號擋不住——留著的話含 `%PATH%` 的
        需求會被換成環境變數的值再交給 CLI。"""
        out = conpty._quote_for_batch("報表 %PATH% 完成")
        assert "%" not in out and "％" in out

    def test_native_exe_keeps_msvcrt_quoting(self):
        """原生 exe 不經 cmd，需求字串要能原樣送達，不該被多改一個字。"""
        line = conpty.build_cmdline([r"C:\x\claude.exe", "價格 $100 & 交期 <7 天"])
        assert "價格 $100 & 交期 <7 天" in line

    def test_empty_argv(self):
        assert conpty.build_cmdline([]) == ""

    @pytest.mark.skipif(not IS_WIN, reason="cmd.exe 轉交是 Windows 專屬行為")
    @pytest.mark.skipif(not conpty.pty_available(), reason="這台機器開不了 PTY")
    def test_metacharacters_in_prompt_do_not_execute(self, monkeypatch, tmp_path):
        """端到端：把注入 payload 當 prompt 丟進 `.cmd` 蓋子，不可以真的執行。

        判準是「有沒有生出那個檔」——不靠讀輸出（console 繼承會讓輸出跑掉）。
        """
        binv = tmp_path / "bin"
        binv.mkdir()
        (binv / "fakecli.cmd").write_text(
            "@echo off\r\nping -n 3 127.0.0.1 >nul\r\n", encoding="utf-8")
        monkeypatch.setenv("PATH", str(binv) + os.pathsep + os.environ.get("PATH", ""))

        # 路徑刻意不含空白：有空白會被 list2cmdline 加引號，就測不到東西了
        target = tmp_path / "M"
        target.mkdir()
        marker = target / "pwned.txt"
        payload = f"x&echo>{marker}"

        sess = conpty.PtySession.spawn(["fakecli", payload], cwd=str(tmp_path))
        try:
            time.sleep(2.0)
        finally:
            sess.close()
        time.sleep(0.5)
        assert not marker.exists(), f"prompt 裡的指令被執行了：{marker}"
