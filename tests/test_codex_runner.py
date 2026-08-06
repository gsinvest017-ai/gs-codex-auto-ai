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

import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location("codex_runner", RUNNER)
codex_runner = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(codex_runner)


def test_resolve_codex_uses_full_path(monkeypatch):
    """Windows npm shim：解析成完整路徑（裸名 'codex' 會 WinError 2）。"""
    monkeypatch.setattr(codex_runner.shutil, "which",
                        lambda e: r"C:\Users\x\AppData\Roaming\npm\codex.CMD")
    assert codex_runner.resolve_codex() == [r"C:\Users\x\AppData\Roaming\npm\codex.CMD"]


def test_resolve_codex_fallback_bare_name(monkeypatch):
    monkeypatch.setattr(codex_runner.shutil, "which", lambda e: None)
    assert codex_runner.resolve_codex() == ["codex"]


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


# ── 多行 prompt 被 cmd.exe 截斷（流水線回報的框架 bug #1）────────────────────
import os
import shutil

import pytest

MULTILINE = "第一行：這是任務標題\n第二行：這行以前整段消失\nDONE_MARKER"

SHIM = (
    "@ECHO off\r\n"
    "GOTO start\r\n"
    ":find_dp0\r\n"
    "SET dp0=%~dp0\r\n"
    "EXIT /b\r\n"
    ":start\r\n"
    "SETLOCAL\r\n"
    "CALL :find_dp0\r\n"
    'IF EXIST "%dp0%\\node.exe" (\r\n'
    '  SET "_prog=%dp0%\\node.exe"\r\n'
    ") ELSE (\r\n"
    '  SET "_prog=node"\r\n'
    "  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n"
    ")\r\n"
    "endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "
    '"%_prog%"  "%dp0%\\node_modules\\pkg\\bin\\entry.js" %*\r\n'
)

ENTRY_JS = (
    "const fs = require('fs');\n"
    "fs.writeFileSync(process.argv[2], JSON.stringify(process.argv.slice(3)), 'utf8');\n"
)


def _make_npm_install(tmp_path):
    """做一份跟真的 npm 安裝同構的假 codex：.CMD shim + node_modules/…/entry.js。"""
    binj = tmp_path / "node_modules" / "pkg" / "bin"
    binj.mkdir(parents=True)
    (binj / "entry.js").write_text(ENTRY_JS, encoding="utf-8")
    shim = tmp_path / "fake.CMD"
    shim.write_text(SHIM, encoding="utf-8")
    return shim, binj / "entry.js"


needs_windows_node = pytest.mark.skipif(
    os.name != "nt" or not shutil.which("node"),
    reason="這條驗的是 Windows 上 cmd.exe 對命令列換行的處理，需要 node",
)


@needs_windows_node
def test_resolve_codex_unwraps_npm_shim_to_node(tmp_path, monkeypatch):
    shim, entry = _make_npm_install(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    got = codex_runner.resolve_codex("fake")
    assert len(got) == 2 and got[0].lower().endswith("node.exe")
    assert Path(got[1]) == entry, f"應該直接指向 .js，實際：{got}"
    assert not str(got).lower().endswith(".cmd")


@needs_windows_node
def test_multiline_prompt_survives_via_node_but_not_via_cmd(tmp_path):
    """對照組證明這個 bug 是真的：同一段多行 prompt，走 .CMD 會被截、走 node 完整。"""
    shim, entry = _make_npm_install(tmp_path)

    # A. 走 .CMD（修正前的路徑）——cmd.exe 會把命令列截在換行處
    out_cmd = tmp_path / "via_cmd.json"
    rc = subprocess.run([str(shim), str(out_cmd), MULTILINE],
                        capture_output=True, timeout=120).returncode
    via_cmd = json.loads(out_cmd.read_text(encoding="utf-8")) if out_cmd.exists() else None

    # B. 走 node（修正後的路徑）
    out_node = tmp_path / "via_node.json"
    node = shutil.which("node")
    subprocess.run([node, str(entry), str(out_node), MULTILINE],
                   capture_output=True, timeout=120, check=True)
    via_node = json.loads(out_node.read_text(encoding="utf-8"))

    assert via_node == [MULTILINE], f"走 node 應該原樣送達，實際：{via_node!r}"

    truncated = via_cmd is None or via_cmd != [MULTILINE]
    assert truncated, (
        "對照組沒壞掉——若 cmd.exe 其實傳得好好的，這個修正就不必要了。"
        f"（rc={rc} via_cmd={via_cmd!r}）"
    )


def test_unwrap_returns_none_for_non_npm_batch(tmp_path):
    """不是 npm shim 的 .CMD 要安全退回，不能亂拆。"""
    other = tmp_path / "other.cmd"
    other.write_text("@echo off\r\necho hi\r\n", encoding="utf-8")
    assert codex_runner.unwrap_npm_shim(other) is None


def test_unwrap_returns_none_when_js_missing(tmp_path):
    """shim 指到的 .js 不存在時不要回一個跑不動的 argv。"""
    shim = tmp_path / "broken.CMD"
    shim.write_text(SHIM, encoding="utf-8")   # 沒有建 node_modules
    assert codex_runner.unwrap_npm_shim(shim) is None
