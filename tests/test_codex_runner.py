"""codex_runner 防掛外殼測試——假 codex 腳本，不需真 codex/額度。

情境對應 2026-07-04 ledger-cli e2e 的三型掛死：
* no-session（openai/codex#20919 stdin 掛死）→ grace 判死、重派、最終 failed
* 停寫型（session 起了 mtime 靜止）→ heartbeat 判死、重派後成功
* exit 0 但沒產出（謊報）→ expect 驗證擋下
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

RUNNER = Path(__file__).resolve().parent.parent / "tools" / "codex_runner.py"


def _fake(tmp: Path, name: str, body: str) -> str:
    p = tmp / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return f'"{sys.executable}" "{p}"'


def _run(tmp: Path, codex_cmd: str, *extra: str):
    sess = tmp / "sessions"
    sess.mkdir(exist_ok=True)
    cwd = tmp / "proj"
    cwd.mkdir(exist_ok=True)
    import os
    env = dict(os.environ, CODEX_RUNNER_SESSIONS_DIR=str(sess))
    r = subprocess.run(
        [sys.executable, str(RUNNER), "--prompt", "x", "--cwd", str(cwd),
         "--session-grace", "2", "--heartbeat", "3", "--retry-backoff", "0.2",
         "--codex-cmd", codex_cmd, *extra],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])


def test_ok_first_try(tmp_path):
    cmd = _fake(tmp_path, "ok.py", f"""
        import pathlib
        pathlib.Path(r"{tmp_path / 'sessions'}").mkdir(exist_ok=True)
        pathlib.Path(r"{tmp_path / 'sessions' / 'a.jsonl'}").write_text("x")
        pathlib.Path(r"{tmp_path / 'proj'}").mkdir(exist_ok=True)
        pathlib.Path(r"{tmp_path / 'proj' / 'out.py'}").write_text("done")
    """)
    rc, res = _run(tmp_path, cmd, "--expect", "out.py", "--retries", "2")
    assert rc == 0 and res["status"] == "ok" and res["attempts"] == 1


def test_no_session_hang_killed_and_failed(tmp_path):
    cmd = _fake(tmp_path, "hang.py", "import time\ntime.sleep(60)\n")
    rc, res = _run(tmp_path, cmd, "--expect", "never.py", "--retries", "2")
    assert rc == 1 and res["status"] == "failed"
    assert res["attempts"] == 2 and "no-session" in res["reason"]


def test_stall_then_retry_succeeds(tmp_path):
    counter = tmp_path / "count.txt"
    cmd = _fake(tmp_path, "stall.py", f"""
        import pathlib, time
        c = pathlib.Path(r"{counter}")
        n = int(c.read_text()) if c.exists() else 0
        c.write_text(str(n + 1))
        pathlib.Path(r"{tmp_path / 'sessions'}").mkdir(exist_ok=True)
        pathlib.Path(r"{tmp_path / 'sessions'}").joinpath(f"s{{n}}.jsonl").write_text("x")
        if n == 0:
            time.sleep(60)
        pathlib.Path(r"{tmp_path / 'proj'}").mkdir(exist_ok=True)
        pathlib.Path(r"{tmp_path / 'proj' / 'out3.py'}").write_text("done")
    """)
    rc, res = _run(tmp_path, cmd, "--expect", "out3.py", "--retries", "3")
    assert rc == 0 and res["status"] == "ok" and res["attempts"] == 2


def test_exit0_without_expect_fails(tmp_path):
    cmd = _fake(tmp_path, "lie.py", f"""
        import pathlib
        pathlib.Path(r"{tmp_path / 'sessions'}").mkdir(exist_ok=True)
        pathlib.Path(r"{tmp_path / 'sessions' / 'lie.jsonl'}").write_text("x")
    """)
    rc, res = _run(tmp_path, cmd, "--expect", "missing.py", "--retries", "2")
    assert rc == 1 and res["status"] == "failed" and "expects_ok=False" in res["reason"]
