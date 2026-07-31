#!/usr/bin/env python3
"""
codex_runner.py — codex exec 的防掛外殼：預防（stdin=DEVNULL）+ 治療（心跳看門狗＋重派）。

為什麼需要（2026-07-04 ledger-cli e2e 實測掛住 6 次）：
* 預防：`codex exec` 在非互動子 shell 會等 stdin EOF 永遠等不到而沉默掛死
  （openai/codex#20919——無 log、無 rollout、無 exit code；retry 同樣掛）。
  本 runner 以 stdin=DEVNULL 啟動子行程，根治此型。
* 治療：「session 起了但停寫」型（大 repo git 狀態 #20200、網路等）→ 以
  ~/.codex/sessions rollout 檔 mtime 當心跳：
    - 啟動後 --session-grace 秒內全域無新 session → 判死
    - 有 session 但最新 mtime 靜止 --heartbeat 秒且行程未結束 → 判死
  判死即殺行程樹並重派（≤ --retries 次）。
* 成功判定以 --expect 檔案落地為主（全部存在才算 ok）；exit 0 但缺檔也重派。

用法（skills / builders 一律用這個，不要裸呼叫 codex exec）：
    python tools/codex_runner.py --prompt "你是 function-builder。…" \
        --expect src/pkg/db.py --expect tests/test_db.py [--model gpt-5.5-codex]
輸出單行 JSON：{"status":"ok|failed","attempts":N,"duration_s":S,"reason":"…"}
exit code：ok=0、failed=1。

並行注意：心跳源是「全域最新 session」，多個 runner 並行時互為近似（活的會掩護死的）；
批內建議序列派工（e2e 實測結論），或接受近似並靠 --expect 判成敗。

測試鉤子：--codex-cmd 可換掉底層指令；CODEX_RUNNER_SESSIONS_DIR 可指定 sessions 目錄。
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

IS_WIN = os.name == "nt"


def resolve_codex(exe: str = "codex") -> list[str]:
    """回傳啟動 codex 的 argv 前綴（不含 exec/子參數）。

    Windows 上 npm 把 codex 裝成 `codex.CMD` shim；Python subprocess.Popen 用**裸名**
    'codex'（無 shell）不做 PATHEXT 解析 → WinError 2「找不到指定的檔案」（每個新任務都踩）。
    用 shutil.which 解析成完整路徑（實測完整 .CMD 路徑可被 Popen 直接啟動、且 args 經
    list2cmdline 正確帶入，不必經 cmd.exe 重新解析而衍生引號問題）。找不到就退回裸名。
    """
    resolved = shutil.which(exe)
    return [resolved] if resolved else [exe]


def _sessions_dir() -> Path:
    env = os.environ.get("CODEX_RUNNER_SESSIONS_DIR")
    return Path(env) if env else Path.home() / ".codex" / "sessions"


def _latest_session_mtime(since: float) -> float | None:
    """啟動時間之後有活動的最新 rollout 檔 mtime；沒有回 None。"""
    base = _sessions_dir()
    if not base.exists():
        return None
    latest = None
    for f in base.rglob("*.jsonl"):
        try:
            mt = f.stat().st_mtime
        except OSError:
            continue
        if mt >= since and (latest is None or mt > latest):
            latest = mt
    return latest


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            proc.kill()
    except Exception:  # noqa: BLE001 — 殺不掉也要繼續（行程可能已死）
        pass


def _expects_ok(cwd: Path, expects: list[str]) -> bool:
    return all((cwd / e).exists() for e in expects)


def run_once(cmd: list[str], cwd: Path, expects: list[str],
             session_grace: float, heartbeat: float, poll: float = 2.0) -> tuple[bool, str]:
    """跑一次；回 (成功?, 原因)。"""
    start = time.time()
    proc = subprocess.Popen(
        cmd, cwd=str(cwd),
        stdin=subprocess.DEVNULL,             # 根治 #20919：絕不讓 codex 等 stdin
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=(subprocess.CREATE_NO_WINDOW if IS_WIN else 0),
    )
    session_seen = False
    while True:
        rc = proc.poll()
        if rc is not None:
            if rc == 0 and _expects_ok(cwd, expects):
                return True, "ok"
            return False, f"exit={rc} expects_ok={_expects_ok(cwd, expects)}"
        now = time.time()
        mt = _latest_session_mtime(start - 5)
        if mt is not None:
            session_seen = True
        if not session_seen and now - start > session_grace:
            _kill_tree(proc)
            return False, f"no-session within {session_grace}s（#20919 型掛死）"
        # 停寫判定要**同時**滿足「session 靜止超過 heartbeat」與「這次 attempt 自己也跑了
        # 至少 heartbeat 秒」。少了後者，重派時上一個被殺掉的 attempt 留下的 session 檔
        # 會讓新 attempt 在起跑 0.2 秒內就被誤判停寫、白白燒掉一次重試
        # （`_latest_session_mtime` 的 start-5 寬限擋不住快速重派）。
        if (session_seen and mt is not None
                and now - mt > heartbeat and now - start > heartbeat):
            _kill_tree(proc)
            return False, f"heartbeat stalled {int(now - mt)}s（停寫型掛死）"
        time.sleep(poll)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="codex exec 防掛外殼")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--expect", action="append", default=[],
                    help="成功必須存在的檔案（可多個；相對 --cwd）")
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--retries", type=int, default=3, help="最多嘗試次數（含首次）")
    ap.add_argument("--session-grace", type=float, default=120.0)
    ap.add_argument("--heartbeat", type=float, default=300.0)
    ap.add_argument("--retry-backoff", type=float, default=5.0)
    ap.add_argument("--codex-cmd", default=None,
                    help="覆寫底層指令（測試用），預設 codex exec --full-auto [-m model]")
    args = ap.parse_args(argv)

    cwd = Path(args.cwd).resolve()
    if args.codex_cmd:
        cmd = shlex.split(args.codex_cmd) + [args.prompt]
    else:
        cmd = resolve_codex() + ["exec", "--full-auto"]  # Windows npm shim 解析（見 resolve_codex）
        if args.model:
            cmd += ["-m", args.model]
        cmd += [args.prompt]

    t0 = time.time()
    reason = ""
    for attempt in range(1, max(1, args.retries) + 1):
        ok, reason = run_once(cmd, cwd, args.expect, args.session_grace, args.heartbeat)
        if ok:
            print(json.dumps({"status": "ok", "attempts": attempt,
                              "duration_s": round(time.time() - t0, 1), "reason": "ok"},
                             ensure_ascii=False))
            return 0
        if attempt < args.retries:
            time.sleep(args.retry_backoff)
    print(json.dumps({"status": "failed", "attempts": args.retries,
                      "duration_s": round(time.time() - t0, 1), "reason": reason},
                     ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
