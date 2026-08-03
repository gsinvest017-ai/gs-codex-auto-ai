"""desktop/sessions.py + desktop/termserver.py — 多 session 管理與本機終端機服務。

兩個測試設計上的取捨，先說清楚免得被誤改：

1. **注入一個長壽的測試用 kind**，不直接用內建的 `shell`。在 Windows 上從有 console
   的行程（pytest 就是）啟動裸 `cmd.exe`，會因 console 繼承而在 0.5 秒內自己結束
   （詳見 `desktop/conpty.py` 的說明），session 還沒測到就被 reap 掉。用一個
   `python -c "…sleep…"` 當子行程就與平台/console 無關。

2. **SSE 用原始 socket 讀**，不用 urllib。SSE 是不定長度串流，`http.client` 的
   `readline()` 在這種回應上會卡住；瀏覽器端真正的客戶端是 `EventSource`，
   行為與原始 socket 一致。原始 socket 讀到的就是使用者會拿到的東西。
"""
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

sessions_mod = pytest.importorskip("sessions")
termserver = pytest.importorskip("termserver")
conpty = pytest.importorskip("conpty")

pytestmark = pytest.mark.skipif(not conpty.pty_available(), reason="這台機器開不了 PTY")

LONG_LIVED = [sys.executable, "-u", "-c",
              "import sys,time\n"
              "sys.stdout.write('TESTKIND-READY\\n'); sys.stdout.flush()\n"
              "time.sleep(30)"]


@pytest.fixture
def server(monkeypatch, tmp_path):
    """起一個真的服務，並注入長壽的 `test` kind。"""
    kind = sessions_mod.SessionKind("test", "測試", LONG_LIVED)
    monkeypatch.setitem(sessions_mod.KINDS, "test", kind)
    mgr = sessions_mod.SessionManager(default_cwd=str(tmp_path))
    srv = termserver.TerminalServer(mgr).start()
    yield srv, mgr
    try:
        srv.stop()
    except Exception:  # noqa: BLE001
        pass


def call(srv, path, data=None, token=None, headers=None):
    url = f"http://127.0.0.1:{srv.port}{path}"
    req = urllib.request.Request(url, method="POST" if data is not None else "GET")
    req.add_header("X-Term-Token", srv.token if token is None else token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req, timeout=10) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, {}


# ── 安全（這個服務能生行程，威脅模型比一般面板嚴重）────────────────────────
class TestSecurity:
    def test_missing_token_is_forbidden(self, server):
        srv, _ = server
        assert call(srv, "/api/kinds", token="")[0] == 403

    def test_wrong_token_is_forbidden(self, server):
        srv, _ = server
        assert call(srv, "/api/kinds", token="not-the-token")[0] == 403

    def test_correct_token_allowed(self, server):
        srv, _ = server
        assert call(srv, "/api/kinds")[0] == 200

    def test_non_loopback_host_is_forbidden(self, server):
        """擋 DNS rebinding：惡意網域即使解析到 127.0.0.1，Host 也會是它自己。"""
        srv, _ = server
        code, _ = call(srv, "/api/kinds", headers={"Host": "evil.example.com"})
        assert code == 403

    def test_path_traversal_blocked(self, server):
        srv, _ = server
        assert call(srv, "/vendor/../../CLAUDE.md")[0] in (403, 404)

    def test_only_known_kinds_can_spawn(self, server):
        """不接受任意 argv——否則打得到這個 port 就等於任意程式執行。"""
        srv, _ = server
        code, body = call(srv, "/api/sessions", {"kind": "rm -rf /"})
        assert code == 400
        assert "未知" in body.get("error", "")

    def test_server_binds_loopback_only(self, server):
        srv, _ = server
        assert srv._httpd.server_address[0] == "127.0.0.1"

    def test_token_is_not_trivially_guessable(self, server):
        srv, _ = server
        assert len(srv.token) >= 32


# ── session 生命週期 ────────────────────────────────────────────────────────
class TestSessions:
    def test_create_list_close(self, server):
        srv, mgr = server
        code, s = call(srv, "/api/sessions", {"kind": "test"})
        assert code == 201, s
        sid = s["id"]
        code, lst = call(srv, "/api/sessions")
        assert sid in [x["id"] for x in lst["sessions"]]
        assert call(srv, f"/api/sessions/{sid}/close", {})[1]["closed"] is True
        code, lst = call(srv, "/api/sessions")
        assert sid not in [x["id"] for x in lst["sessions"]]

    def test_multiple_tabs_coexist(self, server):
        """分頁管理的核心：多個 session 同時活著、關掉一個不影響其他。"""
        srv, mgr = server
        ids = [call(srv, "/api/sessions", {"kind": "test"})[1]["id"] for _ in range(3)]
        time.sleep(0.5)
        code, lst = call(srv, "/api/sessions")
        assert sum(1 for s in lst["sessions"] if s["alive"]) == 3
        call(srv, f"/api/sessions/{ids[1]}/close", {})
        time.sleep(0.3)
        code, lst = call(srv, "/api/sessions")
        alive = {s["id"] for s in lst["sessions"] if s["alive"]}
        assert ids[0] in alive and ids[2] in alive and ids[1] not in alive

    def test_max_sessions_enforced(self, monkeypatch, tmp_path):
        kind = sessions_mod.SessionKind("test", "測試", LONG_LIVED)
        monkeypatch.setitem(sessions_mod.KINDS, "test", kind)
        mgr = sessions_mod.SessionManager(default_cwd=str(tmp_path), max_sessions=2)
        try:
            mgr.create("test"); mgr.create("test")
            with pytest.raises(RuntimeError, match="上限"):
                mgr.create("test")
        finally:
            mgr.close_all()

    def test_close_all_reaps_everything(self, monkeypatch, tmp_path):
        """App 結束時一定要收乾淨，否則留一堆孤兒行程。"""
        kind = sessions_mod.SessionKind("test", "測試", LONG_LIVED)
        monkeypatch.setitem(sessions_mod.KINDS, "test", kind)
        mgr = sessions_mod.SessionManager(default_cwd=str(tmp_path))
        made = [mgr.create("test") for _ in range(3)]
        assert mgr.close_all() == 3
        time.sleep(0.4)
        assert all(not s.alive for s in made)
        assert mgr.list() == []

    def test_input_and_resize_endpoints(self, server):
        srv, _ = server
        sid = call(srv, "/api/sessions", {"kind": "test"})[1]["id"]
        assert call(srv, f"/api/sessions/{sid}/input", {"data": "x"})[0] == 200
        assert call(srv, f"/api/sessions/{sid}/resize", {"cols": 120, "rows": 40})[0] == 200

    def test_unknown_session_returns_404(self, server):
        srv, _ = server
        assert call(srv, "/api/sessions/nope/input", {"data": "x"})[0] == 404
        assert call(srv, "/api/sessions/nope/resize", {"cols": 80, "rows": 24})[0] == 404

    def test_missing_binary_reports_409(self, monkeypatch, server):
        """claude/codex 沒安裝時要給可行動的訊息，不是 500。"""
        srv, _ = server
        bad = sessions_mod.SessionKind("nope", "不存在", ["definitely-not-real-exe-xyz"])
        monkeypatch.setitem(sessions_mod.KINDS, "nope", bad)
        code, body = call(srv, "/api/sessions", {"kind": "nope"})
        assert code == 409
        assert "設定 / 修復" in body.get("error", "")


# ── SSE 串流 ────────────────────────────────────────────────────────────────
class TestStream:
    def _read_sse(self, srv, sid, want=b"event: data", limit=6.0):
        s = socket.create_connection(("127.0.0.1", srv.port), timeout=limit)
        try:
            s.sendall((f"GET /api/sessions/{sid}/stream?token={srv.token} HTTP/1.1\r\n"
                       f"Host: 127.0.0.1:{srv.port}\r\n"
                       f"Accept: text/event-stream\r\n\r\n").encode())
            buf, end = b"", time.time() + limit
            while time.time() < end:
                try:
                    d = s.recv(4096)
                except socket.timeout:
                    break
                if not d:
                    break
                buf += d
                if want in buf and buf.count(b"\n\n") >= 2:
                    break
            return buf
        finally:
            s.close()

    def test_stream_delivers_output(self, server):
        srv, mgr = server
        sid = call(srv, "/api/sessions", {"kind": "test"})[1]["id"]
        time.sleep(1.0)                      # 讓子行程把 READY 印出來
        buf = self._read_sse(srv, sid)
        assert b"HTTP/1.1 200" in buf
        assert b"text/event-stream" in buf
        assert b"event: data" in buf

    def test_stream_payload_is_base64_decodable(self, server):
        """pty 輸出是任意 bytes（含不完整 UTF-8），必須用 base64 過 SSE。"""
        import base64
        import re
        srv, _ = server
        sid = call(srv, "/api/sessions", {"kind": "test"})[1]["id"]
        time.sleep(1.0)
        buf = self._read_sse(srv, sid)
        payloads = re.findall(rb'"b64": "([^"]+)"', buf)
        assert payloads, buf[:400]
        decoded = b"".join(base64.b64decode(p) for p in payloads)
        assert decoded            # 至少要有 ConPTY 自己發的初始化序列

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows console 繼承：pytest 有 console，子行程會寫到那裡而不是 pty，"
               "拿不到內容。這是環境假象不是程式問題（見 desktop/conpty.py），"
               "Linux CI 上這條會真的驗到。")
    def test_stream_carries_child_stdout(self, server):
        """子行程真正印出來的東西要能一路傳到前端。"""
        import base64
        import re
        srv, _ = server
        sid = call(srv, "/api/sessions", {"kind": "test"})[1]["id"]
        time.sleep(1.0)
        buf = self._read_sse(srv, sid)
        decoded = b"".join(base64.b64decode(p)
                           for p in re.findall(rb'"b64": "([^"]+)"', buf))
        assert b"TESTKIND-READY" in decoded

    def test_stream_of_unknown_session_404(self, server):
        srv, _ = server
        buf = self._read_sse(srv, "nosuch", want=b"404")
        assert b"404" in buf


# ── 靜態資產（前端要拿得到 vendored xterm.js）────────────────────────────
class TestStatic:
    @pytest.mark.parametrize("path,needle", [
        ("/", b"CodexAutoAI"),
        ("/vendor/xterm.js", b"Terminal"),
        ("/vendor/xterm.css", b".xterm"),
        ("/vendor/addon-fit.js", b"FitAddon"),
    ])
    def test_assets_served_without_token(self, server, path, needle):
        """**不帶 token** 取靜態資產——這才是瀏覽器實際的行為。

        瀏覽器載入 `<script src>` / `<link href>` 時不會帶自訂標頭，URL 上也沒有
        query。先前這裡手動加了 `?token=`，測試綠但真的用瀏覽器開會整頁 403、
        終端機完全打不開——是實際跑 playwright 才抓到的。這條測試現在按瀏覽器
        的方式取，避免同樣的假綠再發生。
        """
        srv, _ = server
        url = f"http://127.0.0.1:{srv.port}{path}"
        with urllib.request.urlopen(url, timeout=10) as f:
            body = f.read()
            status = f.status
        assert status == 200
        assert needle in body

    def test_api_still_requires_token(self, server):
        """放寬靜態資產不能把 /api/* 也放掉——那裡才是能生行程的邊界。"""
        srv, _ = server
        for path in ("/api/kinds", "/api/sessions"):
            req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}")
            with pytest.raises(urllib.error.HTTPError) as ei:
                urllib.request.urlopen(req, timeout=10)
            assert ei.value.code == 403, path

    def test_static_still_blocks_non_loopback_host(self, server):
        """靜態資產免 token，但 DNS rebinding 的 Host 檢查仍要擋。"""
        srv, _ = server
        req = urllib.request.Request(f"http://127.0.0.1:{srv.port}/vendor/xterm.js")
        req.add_header("Host", "evil.example.com")
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=10)
        assert ei.value.code == 403


# ── 併發（review #45 抓到的 TOCTOU）──────────────────────────────────────────
class TestConcurrency:
    def test_max_sessions_holds_under_concurrent_create(self, monkeypatch, tmp_path):
        """上限檢查與佔位必須原子化。

        原本檢查在鎖裡、spawn 在鎖外，併發請求會全部通過檢查，生出比上限更多的
        實際行程。服務是 ThreadingHTTPServer，這條路真的到得了；上限是防資源
        耗盡的守衛，破了等於沒有。
        """
        import threading
        kind = sessions_mod.SessionKind("test", "測試", LONG_LIVED)
        monkeypatch.setitem(sessions_mod.KINDS, "test", kind)
        cap = 3
        mgr = sessions_mod.SessionManager(default_cwd=str(tmp_path), max_sessions=cap)
        made, errors = [], []
        start = threading.Event()

        def worker():
            start.wait()
            try:
                made.append(mgr.create("test"))
            except RuntimeError:
                errors.append(1)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        start.set()                      # 一起衝，最大化競態機會
        for t in threads:
            t.join(timeout=30)
        try:
            assert len(made) <= cap, f"生出 {len(made)} 個 session，超過上限 {cap}"
            assert len(made) + len(errors) == 12
            assert all(e == 1 for e in errors), f"出現非預期例外：{errors}"
        finally:
            mgr.close_all()

    def test_newer_stream_supersedes_older(self, server):
        """EventSource 斷線重連時，舊串流迴圈要退出，否則兩個 handler 會把輸出切兩半。"""
        srv, mgr = server
        sid = call(srv, "/api/sessions", {"kind": "test"})[1]["id"]
        sess = mgr.get(sid)
        g1 = sess.new_stream()
        g2 = sess.new_stream()
        assert g2 > g1
        assert sess.stream_gen == g2      # 舊的 g1 會看到不相等而自行結束


class TestHandoff:
    """token 不可以出現在交給外部行程的 URL 上。

    內嵌終端機是用 `msedge.exe --app=<url>` 開頁面的，Windows 上同機任何帳號都讀得到
    別人的命令列（工作管理員的「命令列」欄、`Get-CimInstance Win32_Process`）。
    token 擺在那裡等於公開，而這個服務**能生出行程**。所以改成給一枚用過即丟的
    handoff nonce，頁面載入時才把真 token 注進 HTML。
    """

    @staticmethod
    def _page(srv, query=""):
        url = f"http://127.0.0.1:{srv.port}/{query}"
        with urllib.request.urlopen(url, timeout=10) as f:
            return f.status, f.read().decode("utf-8")

    def test_handoff_url_does_not_leak_the_token(self, server):
        srv, _ = server
        assert srv.token not in srv.handoff_url

    def test_each_open_gets_a_fresh_handoff(self, server):
        """開啟失敗時下一次不能拿到已作廢的券，所以每次都要發新的。"""
        srv, _ = server
        assert srv.handoff_url != srv.handoff_url

    def test_valid_handoff_injects_the_token(self, server):
        srv, _ = server
        nonce = srv.new_handoff()
        code, body = self._page(srv, f"?handoff={nonce}")
        assert code == 200
        assert "__TERM_TOKEN__" in body and srv.token in body

    def test_handoff_is_single_use(self, server):
        """用過就死——之後才讀到命令列的人拿不到任何東西。"""
        srv, _ = server
        nonce = srv.new_handoff()
        assert srv.token in self._page(srv, f"?handoff={nonce}")[1]
        assert srv.token not in self._page(srv, f"?handoff={nonce}")[1]

    def test_wrong_handoff_serves_page_without_token(self, server):
        """頁面本身是公開資產，照送；沒 token 的話所有 /api/* 都會 403。"""
        srv, _ = server
        code, body = self._page(srv, "?handoff=not-a-real-nonce")
        assert code == 200 and srv.token not in body

    def test_page_without_handoff_has_no_token(self, server):
        srv, _ = server
        code, body = self._page(srv)
        assert code == 200 and srv.token not in body

    def test_token_alone_is_still_useless_without_api_access(self, server):
        """守住真正的邊界：拿不到 token 就開不了 session。"""
        srv, _ = server
        code, _ = call(srv, "/api/sessions", {"kind": "test"}, token="wrong")
        assert code == 403

    def test_overflow_evicts_oldest_not_everything(self, server):
        """滿了要淘汰**最舊的**，不能整包清掉。

        整包清會把「剛發出去、頁面正要拿來用」的那張也作廢，使用者就會看到一個
        連不上服務的終端機。要驗出差別得看**中間**那張：溢位一格時，淘汰最舊的
        會留下它，整包清則只剩最後一張。
        """
        srv, _ = server
        oldest = srv.new_handoff()                     # #1
        second = srv.new_handoff()                     # #2
        rest = [srv.new_handoff() for _ in range(srv.MAX_HANDOFFS - 1)]   # #3..#33
        assert srv.token not in self._page(srv, f"?handoff={oldest}")[1],             "溢位一格，最舊的應被淘汰"
        assert srv.token in self._page(srv, f"?handoff={second}")[1],             "只溢位一格，第二張不該被牽連（整包清掉才會）"
        assert srv.token in self._page(srv, f"?handoff={rest[-1]}")[1]

    def test_concurrent_redemption_has_exactly_one_winner(self, server, monkeypatch):
        """同一張券被多執行緒同時兌換時，只能有一個拿到 token。

        這是**安全敏感的併發**：服務是 ThreadingHTTPServer，兩個請求同時打進
        `_page()` 是真的到得了的路徑，而這種地方壞掉不會有任何徵兆——只會安靜地
        多發一份能生出行程的 token。

        **必須人工把臨界區撐開**才驗得出東西：原生的「比對→移除」之間只隔幾個
        bytecode，在 GIL 底下幾乎撞不到，拿掉鎖測試照樣全綠（實測過）。這裡把
        比對函式換成「比中就先睡一下」，讓兩條執行緒一定同時站在移除之前——
        拿掉 `_handoff_lock` 這條就會紅。
        """
        import threading
        srv, _ = server
        nonce = srv.new_handoff()

        real = termserver.secrets.compare_digest

        def slow_compare(a, b):
            hit = real(a, b)
            if hit and a == nonce:
                time.sleep(0.15)      # 撐開「已比中、還沒移除」的那段
            return hit

        monkeypatch.setattr(termserver.secrets, "compare_digest", slow_compare)

        start = threading.Event()
        results: list[str] = []        # "token" / "clean" / "error"
        lock = threading.Lock()

        def race():
            start.wait()
            try:
                code, body = self._page(srv, f"?handoff={nonce}")
                r = "token" if srv.token in body else ("clean" if code == 200 else "error")
            except Exception:
                r = "error"
            with lock:
                results.append(r)

        threads = [threading.Thread(target=race) for _ in range(6)]
        for t in threads:
            t.start()
        start.set()                    # 一起衝，最大化競態機會
        for t in threads:
            t.join(timeout=30)
        assert len(results) == 6, "有執行緒沒跑完"
        assert results.count("token") == 1,             f"{results.count('token')} 個執行緒拿到 token，應該只有 1 個"
        # 輸掉的要拿到一張**乾淨的**無 token 頁面。沒有鎖時它們會同時通過比對、
        # 接著搶著移除同一張券，第二個之後全部炸成 500——這就是變異測試看的訊號。
        assert results.count("error") == 0, f"出現 {results.count('error')} 個錯誤：{results}"

    def test_non_ascii_handoff_still_serves_the_page(self, server):
        """`/` 這條路徑**不需要任何憑證**就打得到，所以它必須對垃圾輸入免疫。

        `secrets.compare_digest` 對 str 只吃 ASCII，非 ASCII 直接 TypeError；
        而 query 會被 percent-decode，`?handoff=%C3%A9` 就足以觸發。docstring
        明講「沒帶（或已用掉）也照樣送頁面」，炸掉連線就是違反這個保證。
        """
        srv, _ = server
        srv.new_handoff()      # 券池要**非空**，否則根本走不到比對那行（第一版就漏了）
        code, body = self._page(srv, "?handoff=%C3%A9%C3%A8")
        assert code == 200 and srv.token not in body

    def test_non_ascii_token_is_rejected_not_crashed(self, server):
        """同一個地雷也在 token 那條路上——要回 403，不是把連線炸斷。"""
        srv, _ = server
        url = f"http://127.0.0.1:{srv.port}/api/kinds?token=%C3%A9"
        try:
            with urllib.request.urlopen(url, timeout=10) as f:
                code = f.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 403
