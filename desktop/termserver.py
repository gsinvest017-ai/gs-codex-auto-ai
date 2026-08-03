#!/usr/bin/env python3
"""
termserver.py — 內嵌終端機的本機 HTTP 服務，**純標準庫**。

前端是 xterm.js（vendored，不走 CDN），後端就是這支：送 UI、用 SSE 把 pty 輸出
串出去、用 POST 收按鍵與 resize。

## 為什麼是 SSE 而不是 WebSocket

標準庫沒有 WebSocket server，要嘛引入第三方（破壞 `desktop/` 的純標準庫不變式）、
要嘛自己實作 RFC 6455 的 framing 與 handshake。終端機的資料流是**高度不對稱**的
——輸出量大且連續，輸入只是零星按鍵——SSE（單向串流）配上 POST 送輸入剛好夠用，
而且 SSE 是純文字協定、`http.server` 直接就能寫。

## 安全（這不是普通的 dashboard）

這個服務**能生出行程**，所以威脅模型比一般本機面板嚴重：瀏覽器裡任何一個網頁都
能對 `127.0.0.1` 發請求。四道防線：

1. **只綁 127.0.0.1**，且 port 隨機。
2. **每次啟動產生一次性 token**，所有 API 都要帶；token 只透過 App 自己開啟的
   URL 傳遞。沒有 token 的請求一律 403。
3. **擋 DNS rebinding**：檢查 `Host` 標頭必須是 loopback。惡意網域即使解析到
   127.0.0.1，Host 也會是它自己的網域，這道就擋掉了。
4. **不接受任意 argv**：只能開 `sessions.KINDS` 裡的固定種類（claude / codex /
   shell），呼叫端最多只能附一段需求字串。

另外一律回 `Cache-Control: no-store`，避免 token 出現在磁碟快取。
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

try:
    from . import sessions as sessions_mod  # type: ignore
except ImportError:  # pragma: no cover
    import sessions as sessions_mod         # type: ignore

__all__ = ["TerminalServer"]

def _web_root() -> Path:
    """前端資產目錄。

    凍結（PyInstaller --onefile）時資產被解到 `sys._MEIPASS`，不在原始碼旁邊；
    未凍結時就在 `desktop/web`。兩種佈局都要找得到，否則發行版會變成
    「按了終端機按鈕卻只拿到 404」。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "web"
        if cand.is_dir():
            return cand
    return Path(__file__).resolve().parent / "web"


WEB_ROOT = _web_root()
_LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}
# SSE 輪詢間隔：夠快讓打字看起來即時，又不會空轉燒 CPU。
_POLL = 0.03
_KEEPALIVE = 15.0


class _Handler(BaseHTTPRequestHandler):
    server_version = "CodexAutoAI-Term"
    protocol_version = "HTTP/1.1"

    # -- 基礎工具 -----------------------------------------------------------
    def log_message(self, fmt, *args):  # noqa: A003
        pass  # 不要把每個請求都印到 App 的 stdout

    @property
    def mgr(self) -> "sessions_mod.SessionManager":
        return self.server.manager        # type: ignore[attr-defined]

    def _authorised(self, *, need_token: bool = True) -> bool:
        """Host 必須是 loopback；`need_token` 時再驗 token。

        **靜態資產刻意不要求 token**：瀏覽器載入 `<script src>` / `<link href>`
        時不會帶自訂標頭，URL 上也沒有 query，全部要求 token 的話整頁資產都會 403
        ——實際用瀏覽器開才會發現，只用 curl 手動加 token 測是驗不出來的。

        這樣不會削弱安全性：這些檔案是公開的 MIT 前端資產，不含任何機密也不提供
        任何能力。真正的邊界是 `/api/*`（那裡能生出行程），那條路徑仍然要 token。
        沒有 token 的頁面即使被載入，所有 API 呼叫也都會 403，什麼都做不了。
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in {h.strip("[]") for h in _LOOPBACK}:
            return False
        if not need_token:
            return True
        token = self.server.token        # type: ignore[attr-defined]
        given = self.headers.get("X-Term-Token") or ""
        if not given:
            given = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
        return secrets.compare_digest(given, token)

    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            return {}

    # -- 路由 ---------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        # 靜態資產不要求 token（瀏覽器載入子資源時不會帶標頭），/api/* 才要求。
        is_static = (path in ("/", "/index.html", "/favicon.ico")
                     or path.startswith("/vendor/")
                     or path.startswith("/static/"))
        if not self._authorised(need_token=not is_static):
            self._json(403, {"error": "forbidden"})
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        elif path in ("/", "/index.html"):
            self._static("terminal.html")
        elif path.startswith("/vendor/") or path.startswith("/static/"):
            self._static(path.lstrip("/"))
        elif path == "/api/kinds":
            self._json(200, {"kinds": self.mgr.available_kinds()})
        elif path == "/api/sessions":
            self.mgr.reap()
            self._json(200, {"sessions": [s.as_dict() for s in self.mgr.list()]})
        elif path.startswith("/api/sessions/") and path.endswith("/stream"):
            self._stream(path.split("/")[3])
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._authorised():
            self._json(403, {"error": "forbidden"})
            return
        if path == "/api/sessions":
            self._create()
        elif path.endswith("/input"):
            self._input(path.split("/")[3])
        elif path.endswith("/resize"):
            self._resize(path.split("/")[3])
        elif path.endswith("/close"):
            sid = path.split("/")[3]
            self._json(200, {"closed": self.mgr.close(sid)})
        else:
            self._json(404, {"error": "not found"})

    # -- 靜態檔 -------------------------------------------------------------
    def _static(self, rel: str) -> None:
        # 防目錄穿越：解析後必須仍在 WEB_ROOT 底下
        target = (WEB_ROOT / rel).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self._json(403, {"error": "forbidden"})
            return
        if not target.is_file():
            self._json(404, {"error": "not found"})
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # -- session 操作 --------------------------------------------------------
    def _create(self) -> None:
        b = self._body()
        try:
            sess = self.mgr.create(
                kind=str(b.get("kind") or "shell"),
                prompt=str(b.get("prompt") or ""),
                cwd=b.get("cwd") or None,
                title=b.get("title") or None,
                cols=int(b.get("cols") or 100),
                rows=int(b.get("rows") or 30),
            )
        except sessions_mod.UnknownKind as exc:
            self._json(400, {"error": str(exc)}); return
        except FileNotFoundError as exc:
            self._json(409, {"error": str(exc)}); return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"}); return
        self._json(201, sess.as_dict())

    def _input(self, sid: str) -> None:
        sess = self.mgr.get(sid)
        if sess is None:
            self._json(404, {"error": "no such session"}); return
        self._json(200, {"written": sess.write(str(self._body().get("data") or ""))})

    def _resize(self, sid: str) -> None:
        sess = self.mgr.get(sid)
        if sess is None:
            self._json(404, {"error": "no such session"}); return
        b = self._body()
        sess.resize(int(b.get("cols") or 100), int(b.get("rows") or 30))
        self._json(200, {"ok": True})

    # -- SSE 串流 -----------------------------------------------------------
    def _stream(self, sid: str) -> None:
        sess = self.mgr.get(sid)
        if sess is None:
            self._json(404, {"error": "no such session"}); return

        # 這是不定長度的串流：HTTP/1.1 沒有 Content-Length 又沒有 chunked 的話，
        # 主體框界是未定義的，客戶端會一直等。用 `Connection: close` 把框界定義成
        # 「讀到連線關閉為止」——EventSource 與 urllib 都吃這套，且不必自己實作
        # chunked framing。close_connection 要一併設，否則 handler 會想復用連線。
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(event: str, payload) -> bool:
            try:
                data = json.dumps(payload, ensure_ascii=False)
                self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, ValueError, OSError):
                return False

        # 這條串流的世代。EventSource 斷線會自動重連，舊迴圈可能還沒退出；
        # 兩個 handler 同時 pump 會把輸出切成兩半，所以舊的看到世代變了就結束。
        my_gen = sess.new_stream()

        # 先重播既有畫面，再接即時串流——否則切分頁／重新整理會看到一片空白。
        replay = sess.replay()
        if replay and not emit("data", {"b64": _b64(replay)}):
            return

        last_beat = time.time()
        while not self.server.stopping.is_set():           # type: ignore[attr-defined]
            if sess.stream_gen != my_gen:                  # 有更新的串流接手了
                return
            chunk = sess.pump()
            if chunk:
                if not emit("data", {"b64": _b64(chunk)}):
                    return
                last_beat = time.time()
                continue
            if not sess.alive:
                emit("exit", {"id": sid})
                return
            if time.time() - last_beat > _KEEPALIVE:
                # 沒有輸出也要定期送東西，否則中間層可能把閒置連線斷掉。
                if not emit("ping", {"t": time.time()}):
                    return
                last_beat = time.time()
            time.sleep(_POLL)


def _b64(data: bytes) -> str:
    """pty 輸出是任意 bytes（含不完整的 UTF-8 序列），用 base64 過 SSE 才不會壞。"""
    import base64
    return base64.b64encode(data).decode("ascii")


class TerminalServer:
    """把 SessionManager 包成一個只綁 loopback 的小服務。"""

    def __init__(self, manager: "sessions_mod.SessionManager", *,
                 host: str = "127.0.0.1", port: int = 0) -> None:
        self.manager = manager
        self.token = secrets.token_urlsafe(32)
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.manager = manager          # type: ignore[attr-defined]
        self._httpd.token = self.token         # type: ignore[attr-defined]
        self._httpd.stopping = threading.Event()  # type: ignore[attr-defined]
        self._httpd.daemon_threads = True
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        """帶 token 的入口 URL（只給 App 自己開，不要外流）。"""
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    def start(self) -> "TerminalServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.stopping.set()             # type: ignore[attr-defined]
        try:
            self._httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._httpd.server_close()
        except Exception:  # noqa: BLE001
            pass
        self.manager.close_all()


if __name__ == "__main__":   # 手動試跑：python desktop/termserver.py
    import sys
    import webbrowser
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    mgr = sessions_mod.SessionManager(default_cwd=os.getcwd())
    srv = TerminalServer(mgr).start()
    print("終端機服務:", srv.url)
    webbrowser.open(srv.url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
