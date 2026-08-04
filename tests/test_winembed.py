"""desktop/winembed.py — 把終端機頁面嵌進 App 視窗右欄的純邏輯測試。

真正的 `SetParent` 沒辦法在 CI 上測（要有桌面、要有 Edge），所以這裡鎖住的是
**當初真的踩到、而且會靜默壞掉**的三件事：

1. 抓錯視窗——第一版是「隨便找一個 msedge.exe 的視窗」，結果抓到上一輪殘留的
   Edge 而不是自己啟動的那個。修法是先認 pid 再認 nonce。
2. Edge 用全新 profile 首次啟動會跳「同步瀏覽資料」登入卡，整個蓋住終端機。
   修法是一組抑制旗標——少掉任何一個都是使用者看到一張大白卡。
3. Chromium 在 `--app` 模式自繪標題列（含關閉鈕），要靠 inset 往上裁掉；
   量不到畫布時得回傳 0 讓呼叫端走 DPI 估計值，而不是亂裁。
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

winembed = pytest.importorskip("winembed")

CHROME = "Chrome_WidgetWin_1"


# ── 啟動參數 ────────────────────────────────────────────────────────────────
def test_argv_opens_app_window_with_isolated_profile():
    argv = winembed.build_argv("edge.exe", "http://127.0.0.1:9/?token=t", "/tmp/p")
    assert argv[0] == "edge.exe"
    assert "--app=http://127.0.0.1:9/?token=t" in argv
    assert "--user-data-dir=/tmp/p" in argv


@pytest.mark.parametrize("flag", [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
])
def test_argv_suppresses_browser_own_ui(flag):
    """少任何一個，內嵌的第一眼就是瀏覽器自己的歡迎／登入畫面。"""
    assert flag in winembed.build_argv("edge.exe", "http://x/", "/tmp/p")


def test_argv_disables_implicit_signin():
    """Edge 的「同步您的瀏覽資料」卡片是 msImplicitSignin，實測會蓋住整個終端機。"""
    feats = [a for a in winembed.build_argv("edge.exe", "http://x/", "/tmp/p")
             if a.startswith("--disable-features=")]
    assert feats, "應該要有 --disable-features"
    assert "msImplicitSignin" in feats[0]


# ── nonce ───────────────────────────────────────────────────────────────────
def test_nonce_appends_to_existing_query():
    """終端機 URL 一定帶 token，nonce 不能把它蓋掉。"""
    out = winembed.with_nonce("http://127.0.0.1:1234/?token=abc", "n1")
    assert "token=abc" in out and f"{winembed.NONCE_PARAM}=n1" in out
    assert out.count("?") == 1


def test_nonce_added_when_no_query():
    out = winembed.with_nonce("http://127.0.0.1:1234/", "n1")
    assert out == f"http://127.0.0.1:1234/?{winembed.NONCE_PARAM}=n1"


def test_nonce_goes_before_fragment():
    out = winembed.with_nonce("http://h/?a=1#frag", "n1")
    assert out.endswith("#frag") and f"{winembed.NONCE_PARAM}=n1" in out.split("#")[0]


# ── 選視窗 ──────────────────────────────────────────────────────────────────
def test_picks_our_pid_not_a_leftover_browser():
    """這條是 spike 真的踩過的坑：畫面上還有上一輪殘留的 Edge。

    殘留視窗的標題**也含有 nonce**（同一個 URL 開過），所以只認標題會抓錯；
    必須先認 pid。
    """
    windows = [
        (111, 9999, CHROME, "CodexAutoAI 終端機 ·nonce1"),   # 殘留的
        (222, 4242, CHROME, "CodexAutoAI 終端機 ·nonce1"),   # 我們啟動的
    ]
    assert winembed.pick_window(windows, pid=4242, nonce="nonce1") == 222


def test_falls_back_to_nonce_when_pid_does_not_own_the_window():
    """瀏覽器有時會 re-exec，視窗掛在別的 pid 底下——這時才輪到 nonce。"""
    windows = [(333, 777, CHROME, "CodexAutoAI 終端機 ·abc123")]
    assert winembed.pick_window(windows, pid=4242, nonce="abc123") == 333


def test_ignores_non_chromium_windows():
    windows = [
        (444, 4242, "Notepad", "CodexAutoAI 終端機 ·abc123"),
        (555, 4242, "ConsoleWindowClass", "cmd"),
    ]
    assert winembed.pick_window(windows, pid=4242, nonce="abc123") is None


def test_returns_none_when_nothing_matches():
    windows = [(666, 1, CHROME, "別人的視窗")]
    assert winembed.pick_window(windows, pid=4242, nonce="abc123") is None


def test_nonce_match_needs_a_nonce():
    """nonce 是空字串時不能退化成「隨便挑一個 Chromium 視窗」。"""
    windows = [(777, 1, CHROME, "別人的 Edge")]
    assert winembed.pick_window(windows, pid=4242, nonce="") is None


# ── 標題列裁切 ──────────────────────────────────────────────────────────────
def test_inset_is_distance_from_window_top_to_canvas_top():
    # 視窗 (0,100)-(900,800)、畫布 (0,130)-(900,800) → 自繪標題列 30px
    assert winembed.content_inset((0, 100, 900, 800), (0, 130, 900, 800)) == 30


def test_inset_zero_when_canvas_not_found_yet():
    """量不到就回 0，讓呼叫端改用 DPI 估計值，而不是亂裁掉一塊內容。"""
    assert winembed.content_inset((0, 100, 900, 800), None) == 0


def test_inset_never_negative():
    assert winembed.content_inset((0, 100, 900, 800), (0, 40, 900, 800)) == 0


# ── 平台守門 ────────────────────────────────────────────────────────────────
@pytest.mark.skipif(sys.platform == "win32", reason="這條是驗非 Windows 的降級路徑")
def test_unavailable_off_windows_with_a_reason():
    ok, why = winembed.available()
    assert ok is False and why


class _FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid
        self.killed = False

    def poll(self):
        return None          # 一直活著

    def kill(self):
        self.killed = True


class TestCloseDuringAttach:
    """attach() 最長等 25 秒，期間靠 `pump` 把控制權交回 UI——使用者就在那一刻
    按「收合」或關掉 App 是真的到得了的路徑，而那條路徑會 `close()` 掉同一個
    實例、把 `self.proc` 設成 None。迴圈若直接用 `self.proc.poll()` 就會炸成
    `AttributeError`，而且會被上層的 `except Exception: pass` 吞掉（＝沒有徵兆）。
    """

    @pytest.fixture
    def stubbed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(winembed, "available", lambda: (True, ""))
        monkeypatch.setattr(winembed, "browser_path", lambda: "fake-browser")
        monkeypatch.setattr(winembed.subprocess, "Popen", lambda argv: _FakeProc())
        monkeypatch.setattr(winembed.subprocess, "run", lambda *a, **k: None)
        monkeypatch.setattr(winembed, "_enum_windows", lambda: [])   # 永遠找不到視窗
        monkeypatch.setattr(winembed.tempfile, "gettempdir", lambda: str(tmp_path))
        return winembed.EmbeddedBrowser()

    def test_close_from_pump_does_not_raise(self, stubbed):
        emb = stubbed

        def pump_then_close():
            emb.close()          # 模擬使用者在等待期間按下「收合」

        ok = emb.attach(1234, "http://127.0.0.1:1/", width=800, height=600,
                        timeout=3, pump=pump_then_close)
        assert ok is False
        assert emb.error, "取消也要留下原因，不能靜默"

    def test_cancelled_attach_reports_cancellation(self, stubbed):
        emb = stubbed
        emb.attach(1234, "http://127.0.0.1:1/", width=800, height=600,
                   timeout=3, pump=emb.close)
        assert "取消" in emb.error

    def test_normal_timeout_still_reports_timeout(self, stubbed):
        """沒有人取消時，逾時要照舊講逾時——別把兩種情況混成同一個訊息。"""
        emb = stubbed
        ok = emb.attach(1234, "http://127.0.0.1:1/", width=800, height=600,
                        timeout=1, pump=None)
        assert ok is False and "逾時" in emb.error

    def test_cancel_after_window_found_is_not_reported_as_success(
            self, monkeypatch, stubbed):
        """視窗找到之後、量 inset 的期間被取消——`_measure_inset` 還會再 pump 6 秒。

        這裡漏掉存活確認的話 `attach()` 會回 True，UI 就綠字寫「已嵌在右邊欄位」
        但右欄一片空白，而且 `_embed` 指著一個殭屍實例。
        """
        emb = stubbed
        monkeypatch.setattr(winembed, "_enum_windows",
                            lambda: [(999, 4242, CHROME, "終端機")])
        monkeypatch.setattr(winembed.EmbeddedBrowser, "_reparent",
                            lambda self, h, p: None)
        monkeypatch.setattr(winembed, "_child_windows", lambda h: [])  # 量不到畫布

        calls = {"n": 0}

        def pump():
            calls["n"] += 1
            if calls["n"] >= 2:      # 第 1 次讓它找到視窗，之後才取消
                emb.close()

        ok = emb.attach(1234, "http://127.0.0.1:1/", width=800, height=600,
                        timeout=3, pump=pump)
        assert ok is False, "被取消了卻回報成功"
        assert emb.error
        assert emb.hwnd is None and not emb.alive


class TestOrphanCleanup:
    """使用者回報：按下「開啟內嵌終端機」後跳出**兩個**視窗——一個獨立的終端機
    視窗，加上一個瀏覽器分頁。

    成因是失敗路徑沒把已經啟動的瀏覽器收掉：
      1. `attach()` 一看到 `proc.poll()` 有值就在 0.3 秒內放棄，**而且沒呼叫
         `close()`**；但瀏覽器 handoff（啟動器 stub 生出真正的 browser process
         後自己退場）本來就會讓 poll() 有值，視窗一兩秒後才冒出來。
      2. 就算呼叫了 `close()`，它只 taskkill 我們 Popen 的那個 pid；handoff 之後
         擁有視窗的行程不在那棵樹裡，`/T` 掃不到。
    於是那個視窗變孤兒留在桌面上，呼叫端又去開 fallback 視窗 = 兩個。

    實測（2026-08-04）舊行為殘留 25 個 msedge 行程。
    """

    def test_orphan_pids_matches_our_nonce(self, monkeypatch):
        monkeypatch.setattr(winembed, "_enum_windows", lambda: [
            (1, 111, CHROME, "CodexAutoAI 終端機 ·abc123"),
            (2, 222, CHROME, "別人的 Edge"),
            (3, 333, "Notepad", "abc123"),          # 不是 Chromium，不能碰
        ])
        emb = winembed.EmbeddedBrowser()
        emb.nonce = "abc123"
        assert emb._orphan_pids() == [111]

    def test_empty_nonce_never_sweeps(self, monkeypatch):
        """**這條是安全閥**：nonce 是空字串時 `"" in title` 恆真，掃下去會把
        使用者自己開的每一個 Chromium 視窗都殺掉。"""
        monkeypatch.setattr(winembed, "_enum_windows", lambda: [
            (1, 111, CHROME, "使用者的網銀"),
            (2, 222, CHROME, "使用者的 Gmail"),
        ])
        emb = winembed.EmbeddedBrowser()
        emb.nonce = ""
        assert emb._orphan_pids() == []

    def test_close_kills_handed_off_window_owner(self, monkeypatch, tmp_path):
        """handoff 之後我們手上沒有那個行程的 handle，只剩 nonce——還是要收得掉。"""
        killed = []
        monkeypatch.setattr(winembed, "_kill_tree", killed.append)
        monkeypatch.setattr(winembed, "_enum_windows",
                            lambda: [(1, 999, CHROME, "CodexAutoAI 終端機 ·n1")])
        emb = winembed.EmbeddedBrowser()
        emb.proc = None                    # 啟動器早就退場了
        emb.nonce = "n1"
        emb.profile_dir = str(tmp_path / "p")
        emb.close()
        assert killed == [999]

    def test_close_kills_both_our_pid_and_the_window_owner(self, monkeypatch, tmp_path):
        killed = []
        monkeypatch.setattr(winembed, "_kill_tree", killed.append)
        monkeypatch.setattr(winembed, "_enum_windows",
                            lambda: [(1, 999, CHROME, "CodexAutoAI 終端機 ·n1")])

        class _Live:
            pid = 555

            def poll(self):
                return None

            def kill(self):
                pass

        emb = winembed.EmbeddedBrowser()
        emb.proc = _Live()
        emb.nonce = "n1"
        emb.profile_dir = str(tmp_path / "p")
        emb.close()
        assert killed == [555, 999]

    def test_close_clears_nonce_so_a_second_close_is_a_noop(self, monkeypatch, tmp_path):
        """關閉路徑有兩個入口，close() 會被呼叫兩次——第二次不該再去掃視窗。"""
        killed = []
        monkeypatch.setattr(winembed, "_kill_tree", killed.append)
        monkeypatch.setattr(winembed, "_enum_windows",
                            lambda: [(1, 999, CHROME, "CodexAutoAI 終端機 ·n1")])
        emb = winembed.EmbeddedBrowser()
        emb.proc = None
        emb.nonce = "n1"
        emb.profile_dir = str(tmp_path / "p")
        emb.close()
        emb.close()
        assert killed == [999]

    def test_early_exit_does_not_abort_before_grace(self, monkeypatch, tmp_path):
        """啟動器行程先退場是正常的（handoff），不能在那一瞬間就放棄。

        這裡讓行程「一開始就死」但視窗在第 3 次輪詢才出現——舊行為會在第一次
        輪詢就 return False，新行為要等到視窗出現。
        """
        monkeypatch.setattr(winembed, "available", lambda: (True, ""))
        monkeypatch.setattr(winembed, "browser_path", lambda: "fake")
        monkeypatch.setattr(winembed.tempfile, "gettempdir", lambda: str(tmp_path))
        monkeypatch.setattr(winembed.subprocess, "Popen", lambda argv: _DeadProc())
        monkeypatch.setattr(winembed.EmbeddedBrowser, "_reparent",
                            lambda self, h, p: None)
        monkeypatch.setattr(winembed.EmbeddedBrowser, "_measure_inset",
                            lambda self, pump=None: None)
        monkeypatch.setattr(winembed.EmbeddedBrowser, "fit",
                            lambda self, w, h: None)
        monkeypatch.setattr(winembed, "_window_pid", lambda h: 777)
        # hwnd 是假的，真的 `IsWindow()` 當然說它不存在——把平台旗標關掉讓 `alive`
        # 走非 Windows 分支（有 hwnd 就算活著）。這裡要驗的是「stub 行程退場不影響
        # 判定」，不是 Win32 API 本身。
        monkeypatch.setattr(winembed, "IS_WIN", False)
        # **刻意不 mock `alive`**：第一版就是把它 mock 成 True，結果掩蓋了真正的
        # bug——`alive` 當時看的是「我們 Popen 的那個行程」，而 handoff 的定義就是
        # 那個行程會退場，所以 attach() 最後那道存活確認必然失敗、把剛嵌好的視窗
        # 砍掉。測試綠燈、功能其實沒好。

        calls = {"n": 0}

        def late_windows():
            calls["n"] += 1
            if calls["n"] < 3:
                return []
            return [(42, 777, CHROME, f"CodexAutoAI 終端機 ·{winembed._CURRENT[0]}")]

        # attach 產生的 nonce 只有它自己知道，借一個共用格子把它傳出來
        winembed._CURRENT = [""]
        real_with_nonce = winembed.with_nonce

        def spy(url, nonce):
            winembed._CURRENT[0] = nonce
            return real_with_nonce(url, nonce)

        monkeypatch.setattr(winembed, "with_nonce", spy)
        monkeypatch.setattr(winembed, "_enum_windows", late_windows)

        emb = winembed.EmbeddedBrowser()
        ok = emb.attach(1, "http://127.0.0.1:1/", width=800, height=600, timeout=10)
        assert ok is True, f"提早放棄了：{emb.error!r}"
        assert emb.hwnd == 42


class _DeadProc:
    """啟動器 stub：生出瀏覽器後立刻退場。"""
    pid = 4242

    def poll(self):
        return 0

    def kill(self):
        pass


class TestAliveFollowsTheWindow:
    """`alive` 要看**視窗**，不是我們 Popen 的那個行程。

    handoff 的定義就是「啟動器 stub 生出真正的 browser process 後自己退場」。
    拿 stub 的存活狀態當判準，在 handoff 情境下必然是 False——視窗明明已經嵌好，
    `attach()` 最後那道存活確認還是會把它砍掉。
    """

    def test_dead_stub_but_live_window_counts_as_alive(self, monkeypatch):
        monkeypatch.setattr(winembed, "IS_WIN", False)   # 走非 Windows 的分支
        emb = winembed.EmbeddedBrowser()
        emb.proc = _DeadProc()
        emb.hwnd = 42
        assert emb.alive is True

    def test_without_a_window_it_falls_back_to_the_process(self):
        emb = winembed.EmbeddedBrowser()
        emb.proc = _DeadProc()
        emb.hwnd = None
        assert emb.alive is False

    def test_close_kills_the_window_owner_even_without_a_nonce_match(
            self, monkeypatch, tmp_path):
        """頁面載入完標題就不含 nonce 了，屆時只剩 hwnd 認得出擁有者。"""
        killed = []
        monkeypatch.setattr(winembed, "_kill_tree", killed.append)
        monkeypatch.setattr(winembed, "_enum_windows",
                            lambda: [(1, 999, CHROME, "CodexAutoAI 終端機")])  # 沒有 nonce
        monkeypatch.setattr(winembed, "_is_window", lambda h: True)
        monkeypatch.setattr(winembed, "_window_class", lambda h: CHROME)
        monkeypatch.setattr(winembed, "_window_pid", lambda h: 999)
        emb = winembed.EmbeddedBrowser()
        emb.proc = None
        emb.nonce = "n1"
        emb.hwnd = 4242
        emb.profile_dir = str(tmp_path / "p")
        emb.close()
        assert killed == [999]

    def test_close_does_not_kill_a_recycled_pid(self, monkeypatch, tmp_path):
        """視窗已經不在了就**不准**再去殺 attach 當時記下的那個 pid。

        Windows 很快就會把結束行程的 pid 配給別人，照著殺會誤傷不相干的行程樹。
        """
        killed = []
        monkeypatch.setattr(winembed, "_kill_tree", killed.append)
        monkeypatch.setattr(winembed, "_enum_windows", lambda: [])
        monkeypatch.setattr(winembed, "_is_window", lambda h: False)   # 視窗早就沒了
        monkeypatch.setattr(winembed, "_window_pid",
                            lambda h: pytest.fail("視窗都不在了還去問它的 pid"))
        emb = winembed.EmbeddedBrowser()
        emb.proc = None
        emb.nonce = "n1"
        emb.hwnd = 4242
        emb.profile_dir = str(tmp_path / "p")
        emb.close()
        assert killed == []


class TestGraceExpired:
    """行程死了**而且**寬限期內視窗也沒出現——這才是真正的啟動失敗。

    這條分支最容易安靜地退化（比較符號寫反、`close()` 被拿掉），而退化的後果
    就是原本那個 bug 整個回來：孤兒視窗 + fallback 視窗 = 兩個。
    """

    @pytest.fixture
    def stubbed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(winembed, "available", lambda: (True, ""))
        monkeypatch.setattr(winembed, "browser_path", lambda: "fake")
        monkeypatch.setattr(winembed.tempfile, "gettempdir", lambda: str(tmp_path))
        monkeypatch.setattr(winembed.subprocess, "Popen", lambda argv: _DeadProc())
        monkeypatch.setattr(winembed, "_enum_windows", lambda: [])   # 視窗永遠不出現
        monkeypatch.setattr(winembed, "_HANDOFF_GRACE", 0.5)         # 別讓測試等 6 秒
        return winembed.EmbeddedBrowser()

    def test_reports_failure_after_grace(self, stubbed):
        ok = stubbed.attach(1, "http://127.0.0.1:1/", width=800, height=600, timeout=10)
        assert ok is False
        assert "沒有出現它的視窗" in stubbed.error, stubbed.error

    def test_gives_up_after_grace_not_after_the_full_timeout(self, stubbed):
        """要在寬限期後就放棄，不是苦等到 attach 的 25 秒逾時。"""
        t0 = time.time()
        stubbed.attach(1, "http://127.0.0.1:1/", width=800, height=600, timeout=10)
        assert time.time() - t0 < 5, "拖到逾時才放棄"

    def test_cleans_up_on_grace_failure(self, stubbed, monkeypatch):
        """失敗一定要收乾淨——沒收就是使用者看到的那個孤兒視窗。"""
        killed = []
        monkeypatch.setattr(winembed, "_kill_tree", killed.append)
        stubbed.attach(1, "http://127.0.0.1:1/", width=800, height=600, timeout=10)
        assert stubbed.nonce == "", "close() 沒被呼叫"
        assert stubbed.profile_dir is None


class TestRecycledPidDuringAttach:
    """啟動器退場後它的 pid 隨時會被配給別人。拿那個 pid 去比對視窗，可能挑中
    **不相干的 Chromium 視窗**，然後把別人的瀏覽器 SetParent 進我們的欄位裡。
    行程死了就只認 nonce。"""

    def test_does_not_grab_an_unrelated_window_owned_by_a_recycled_pid(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(winembed, "available", lambda: (True, ""))
        monkeypatch.setattr(winembed, "browser_path", lambda: "fake")
        monkeypatch.setattr(winembed.tempfile, "gettempdir", lambda: str(tmp_path))
        monkeypatch.setattr(winembed.subprocess, "Popen", lambda argv: _DeadProc())
        monkeypatch.setattr(winembed, "_kill_tree", lambda pid: None)
        monkeypatch.setattr(winembed, "_HANDOFF_GRACE", 0.5)
        reparented = []
        monkeypatch.setattr(winembed.EmbeddedBrowser, "_reparent",
                            lambda self, h, p: reparented.append(h))
        # 使用者自己的 Edge，剛好用到被回收的那個 pid，標題**沒有**我們的 nonce
        monkeypatch.setattr(winembed, "_enum_windows",
                            lambda: [(1234, _DeadProc.pid, CHROME, "使用者的網銀")])

        emb = winembed.EmbeddedBrowser()
        ok = emb.attach(1, "http://127.0.0.1:1/", width=800, height=600, timeout=10)
        assert ok is False, "抓到了不屬於我們的視窗"
        assert reparented == [], f"把別人的視窗 reparent 進來了：{reparented}"


def test_close_ignores_a_reused_hwnd_that_is_no_longer_chromium(monkeypatch, tmp_path):
    """HWND 也會被回收（比 pid 罕見）。視窗還在、但已經不是 Chromium 了就別動它。"""
    killed = []
    monkeypatch.setattr(winembed, "_kill_tree", killed.append)
    monkeypatch.setattr(winembed, "_enum_windows", lambda: [])
    monkeypatch.setattr(winembed, "_is_window", lambda h: True)
    monkeypatch.setattr(winembed, "_window_class", lambda h: "Notepad")
    monkeypatch.setattr(winembed, "_window_pid",
                        lambda h: pytest.fail("不是 Chromium 還去問 pid"))
    emb = winembed.EmbeddedBrowser()
    emb.proc = None
    emb.nonce = "n1"
    emb.hwnd = 4242
    emb.profile_dir = str(tmp_path / "p")
    emb.close()
    assert killed == []


class TestCrossProcessFocus:
    """reparent 進別的行程之後鍵盤不會自己過來——前景視窗是 tk 的 toplevel，
    按鍵預設進 tk 的佇列。使用者點了終端機卻打不了字就是這個原因。"""

    def test_focus_targets_the_canvas_not_the_frame(self, monkeypatch):
        """焦點要下在畫布：外框拿到焦點時 Chromium 不一定會轉交給 renderer。"""
        focused = []
        monkeypatch.setattr(winembed, "_focus_across_processes", focused.append)
        monkeypatch.setattr(winembed, "_child_windows",
                            lambda h: [(11, "Intermediate D3D Window"),
                                       (22, "Chrome_RenderWidgetHostHWND")])
        emb = winembed.EmbeddedBrowser()
        emb.hwnd = 99
        emb.focus()
        assert focused == [22]

    def test_falls_back_to_the_frame_when_no_canvas(self, monkeypatch):
        focused = []
        monkeypatch.setattr(winembed, "_focus_across_processes", focused.append)
        monkeypatch.setattr(winembed, "_child_windows", lambda h: [])
        emb = winembed.EmbeddedBrowser()
        emb.hwnd = 99
        emb.focus()
        assert focused == [99]

    def test_no_window_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(winembed, "_focus_across_processes",
                            lambda h: pytest.fail("沒有視窗還去設焦點"))
        winembed.EmbeddedBrowser().focus()

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(winembed, "_child_windows",
                            lambda h: (_ for _ in ()).throw(OSError("boom")))
        emb = winembed.EmbeddedBrowser()
        emb.hwnd = 99
        emb.focus()      # 不該拋
