#!/usr/bin/env python3
"""
sessions.py — 多個 PTY session 的生命週期管理，**純標準庫**。

`conpty.PtySession` 只管「一個」pty。這一層管「一堆」：建立／列出／關閉、
每個 session 的標題與種類（claude / codex / shell），以及**輸出重播緩衝**。

重播緩衝是分頁 UI 的必要條件：瀏覽器分頁切走再切回、或重新整理頁面時，
xterm.js 是全新的空畫面。沒有緩衝就會看到一片空白，像 session 死掉一樣。
所以每個 session 都保留最近 N bytes 的原始輸出，新連上的前端先收到這段重播、
之後才接續即時串流。

執行緒安全：`SessionManager` 的每個公開方法都在鎖裡；`Session.drain()` 由
HTTP 執行緒呼叫，`conpty` 的讀取執行緒只往 queue 塞，兩者不互相阻塞。
"""
from __future__ import annotations

import itertools
import os
import shutil
import threading
import time
from typing import Iterable, Optional

try:                      # 允許以 `python desktop/sessions.py` 或套件方式載入
    from . import conpty  # type: ignore
except ImportError:       # pragma: no cover
    import conpty         # type: ignore

__all__ = ["Session", "SessionManager", "SessionKind", "KINDS", "UnknownKind"]

# 每個 session 保留的重播緩衝上限。夠一頁多的 scrollback，又不會把記憶體吃爆。
REPLAY_LIMIT = 256 * 1024


class UnknownKind(ValueError):
    """要求的 session 種類不在允許清單裡。"""


class SessionKind:
    """一種可啟動的 session。

    **刻意用固定清單而不是讓呼叫端傳 argv**：這層背後會被一個本機 HTTP 服務
    呼叫到，如果允許任意 argv，任何能打到那個 port 的東西（例如瀏覽器裡的
    惡意網頁）就等於拿到任意程式執行。種類固定 + 參數只接受需求字串，
    攻擊面小得多。
    """

    def __init__(self, key: str, label: str, argv: list[str],
                 accepts_prompt: bool = False) -> None:
        self.key = key
        self.label = label
        self.argv = argv
        self.accepts_prompt = accepts_prompt

    def build_argv(self, prompt: str = "") -> list[str]:
        argv = list(self.argv)
        if self.accepts_prompt and prompt:
            argv.append(prompt)
        return argv

    def available(self) -> bool:
        return shutil.which(self.argv[0]) is not None


_SHELL = ["cmd.exe"] if os.name == "nt" else [os.environ.get("SHELL", "/bin/bash")]

# 分頁上顯示的名字刻意寫成**角色**而不是底層 CLI：這條 session 跑的是完整的
# 七階段 pipeline——Claude 只做規劃與調度，實作是它再去叫 `codex exec` 產出的。
# 標成「Claude」會讓人以為程式碼是 Claude 寫的，正好跟本框架的核心分工相反。
KINDS: dict[str, SessionKind] = {
    "claude": SessionKind("claude", "Pipeline", ["claude"], accepts_prompt=True),
    "codex": SessionKind("codex", "Codex（直接）", ["codex"], accepts_prompt=True),
    "shell": SessionKind("shell", "終端機", _SHELL),
}

_ids = itertools.count(1)


class Session:
    """一個具名的 pty session + 供前端重播的輸出緩衝。"""

    def __init__(self, sid: str, kind: SessionKind, title: str,
                 pty: "conpty.PtySession", cwd: str) -> None:
        self.id = sid
        self.kind = kind
        self.title = title
        self.cwd = cwd
        self.created_at = time.time()
        self._pty = pty
        self._buf = bytearray()
        self._lock = threading.Lock()
        # 目前有效的串流世代。EventSource 斷線會自動重連，舊的串流迴圈可能還在跑；
        # 兩個 handler 同時 pump 同一個 session 會把輸出切成兩半（畫面看起來像壞掉）。
        # 新串流進來就 +1，舊的看到世代變了就自己退出。
        self.stream_gen = 0

    # -- 輸出 ---------------------------------------------------------------
    def pump(self) -> bytes:
        """把 pty 新產生的輸出收進重播緩衝並回傳（給串流用）。"""
        chunk = self._pty.read_nowait()
        if chunk:
            with self._lock:
                self._buf.extend(chunk)
                if len(self._buf) > REPLAY_LIMIT:
                    # 從尾端保留：使用者要看的是最近的畫面，不是最早的。
                    del self._buf[:len(self._buf) - REPLAY_LIMIT]
        return chunk

    def new_stream(self) -> int:
        """宣告一條新串流；回傳它的世代編號（舊串流看到編號變了就該退出）。"""
        with self._lock:
            self.stream_gen += 1
            return self.stream_gen

    def replay(self) -> bytes:
        """目前緩衝的全部內容（前端剛連上時先送這個）。"""
        with self._lock:
            return bytes(self._buf)

    # -- 輸入／控制 ----------------------------------------------------------
    def write(self, data: str | bytes) -> int:
        return self._pty.write(data)

    def resize(self, cols: int, rows: int) -> None:
        self._pty.resize(cols, rows)

    def close(self) -> None:
        self._pty.close()

    @property
    def alive(self) -> bool:
        return self._pty.alive

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.key,
            "kind_label": self.kind.label,
            "title": self.title,
            "cwd": self.cwd,
            "alive": self.alive,
            "created_at": self.created_at,
        }


class SessionManager:
    """建立／查詢／關閉多個 session。所有公開方法都是執行緒安全的。"""

    def __init__(self, default_cwd: Optional[str] = None, max_sessions: int = 12) -> None:
        self.default_cwd = default_cwd or os.getcwd()
        self.max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}
        self._pending = 0          # 已通過上限檢查、但 spawn 還沒完成的佔位數
        self._lock = threading.RLock()

    # -- 建立 ---------------------------------------------------------------
    def create(self, kind: str = "shell", *, prompt: str = "",
               cwd: Optional[str] = None, title: Optional[str] = None,
               cols: int = 100, rows: int = 30) -> Session:
        spec = KINDS.get(kind)
        if spec is None:
            raise UnknownKind(f"未知的 session 種類：{kind!r}（可用：{', '.join(KINDS)}）")
        if not spec.available():
            raise FileNotFoundError(
                f"找不到 {spec.argv[0]}——請先在 App 按「設定 / 修復」安裝並登入。")

        # **檢查與佔位必須是同一個原子操作**：spawn() 很慢，若檢查完就放掉鎖，
        # 併發的建立請求會全部通過上限檢查（服務是 ThreadingHTTPServer，這條路
        # 真的到得了），生出比 max_sessions 更多的實際行程。上限是防資源耗盡的
        # 守衛之一，破了就等於沒有。用 _pending 計數在鎖內先佔位，spawn 完再補實體。
        with self._lock:
            live = sum(1 for s in self._sessions.values() if s.alive)
            if live + self._pending >= self.max_sessions:
                raise RuntimeError(f"同時開啟的 session 已達上限 {self.max_sessions}")
            self._pending += 1

        try:
            argv = spec.build_argv(prompt)
            pty = conpty.PtySession.spawn(argv, cwd=cwd or self.default_cwd,
                                          cols=cols, rows=rows)
            sid = f"s{next(_ids)}"
            sess = Session(sid, spec, title or spec.label, pty, cwd or self.default_cwd)
            with self._lock:
                self._sessions[sid] = sess
            return sess
        finally:
            with self._lock:
                self._pending -= 1

    # -- 查詢 ---------------------------------------------------------------
    def get(self, sid: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(sid)

    def list(self) -> list[Session]:
        with self._lock:
            return sorted(self._sessions.values(), key=lambda s: s.created_at)

    def alive_sessions(self) -> Iterable[Session]:
        return [s for s in self.list() if s.alive]

    # -- 關閉 ---------------------------------------------------------------
    def close(self, sid: str) -> bool:
        with self._lock:
            sess = self._sessions.pop(sid, None)
        if sess is None:
            return False
        sess.close()
        return True

    def close_all(self) -> int:
        """關掉全部——App 結束時一定要呼叫，否則會留下一堆孤兒行程。"""
        with self._lock:
            items = list(self._sessions.values())
            self._sessions.clear()
        for s in items:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        return len(items)

    def reap(self) -> int:
        """清掉已經死掉的 session（使用者在裡面打 exit 就會這樣）。"""
        with self._lock:
            dead = [sid for sid, s in self._sessions.items() if not s.alive]
            for sid in dead:
                self._sessions.pop(sid, None)
        return len(dead)

    def available_kinds(self) -> list[dict]:
        return [{"key": k.key, "label": k.label, "available": k.available()}
                for k in KINDS.values()]
