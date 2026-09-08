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


# ── 認得「重試也沒用」的失敗（實測：整條 pipeline 因模型設定壞掉卻查不出原因）──
class TestClassifyFailure:
    """以前 stdout/stderr 直接丟 DEVNULL，codex 印的 400 錯誤全被丟掉，runner 只能回
    `exit=1 expects_ok=True`——使用者看不出是模型設錯、沒登入、還是額度用完。

    實際踩到的情況：`~/.codex/config.toml` 的 model 是 ChatGPT 帳號不支援的型號，
    整條七階段 pipeline 完全跑不動，而錯誤訊息一個字都沒提到模型。
    """

    REAL_400 = (
        'ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error",'
        '"message":"The \'gpt-5.6-sol\' model is not supported when using Codex with '
        'a ChatGPT account."}}'
    )

    def test_model_not_supported_names_the_model_and_the_account(self):
        kind, hint = codex_runner.classify_failure(self.REAL_400)
        assert kind == "model_not_supported"
        assert "gpt-5.6-sol" in hint, "要指名是哪個模型，不然使用者不知道改什麼"
        assert "ChatGPT" in hint
        assert "config.toml" in hint, "要講怎麼修"

    def test_says_retrying_will_not_help(self):
        """這決定要不要重試三次——設定問題重跑只會得到三次一樣的錯誤。"""
        _, hint = codex_runner.classify_failure(self.REAL_400)
        assert "重試不會有幫助" in hint

    def test_not_logged_in(self):
        kind, hint = codex_runner.classify_failure("Error: not logged in. Please run `codex login`.")
        assert kind == "not_logged_in" and "codex login" in hint

    def test_ordinary_failure_is_left_alone(self):
        """一般失敗要照原本的方式重試——別把暫時性故障誤判成不可恢復。"""
        assert codex_runner.classify_failure("some transient network hiccup") == ("", "")
        assert codex_runner.classify_failure("") == ("", "")

    def test_does_not_match_a_mere_mention_of_the_word_model(self):
        """反向保護：光是出現 model 這個字不能算——會把正常輸出誤判成致命錯誤。"""
        assert codex_runner.classify_failure("the model wrote 3 files") == ("", "")


class TestFatalStopsRetrying:
    def test_fatal_prefix_is_what_main_keys_off(self):
        """`fatal:` 前綴是 run_once 與 main 之間的契約，改名兩邊要一起改。"""
        src = (Path(__file__).resolve().parent.parent / "tools" / "codex_runner.py").read_text(
            encoding="utf-8")
        assert 'reason = f"fatal:{kind} {hint}"' in src
        assert 'if reason.startswith("fatal:"):' in src, "main 必須在 fatal 時停止重試"


def test_output_is_captured_to_a_file_not_a_pipe():
    """要輸出才能講清楚失敗原因，但 PIPE 寫滿沒人讀就會把 codex 卡住——

    而這支 runner 的整個存在意義就是不要讓 codex 掛死。所以只能導到檔案。
    """
    src = (Path(__file__).resolve().parent.parent / "tools" / "codex_runner.py").read_text(
        encoding="utf-8")
    assert "stdout=fh" in src and "stderr=subprocess.STDOUT" in src
    assert "stdout=subprocess.PIPE" not in src, "PIPE 會在緩衝區寫滿時阻塞子行程"
    assert "os.close(fd)" in src, "mkstemp 的 fd 不關掉，Windows 會刪不掉暫存檔"


class TestLooseFatalPatternsDoNotFireOnNormalOutput:
    """`not logged in` / `quota` / `rate limit` 這些字，Codex 在**正常產出**裡就會寫

    ——它的工作就是讀寫與討論程式碼。若整包輸出裡出現這些字就判成不可恢復，一次
    可重試的失敗會被白白放棄掉剩下的重試預算。所以只在「看起來像錯誤的行」裡比對。
    """

    def test_model_discussing_rate_limiting_is_not_a_quota_failure(self):
        out = ("I added a token bucket to handle the API's rate limit, and documented\n"
               "the quota headers in README.md. Then the build broke for another reason.\n")
        assert codex_runner.classify_failure(out) == ("", "")

    def test_code_mentioning_not_logged_in_is_not_an_auth_failure(self):
        out = 'Wrote: if (!session) { return res.status(401).json({error_message: "not logged in"}) }\n'
        # 這行雖然含 error_message，但那是**產出的程式碼**，不是 codex 自己的錯誤
        kind, _ = codex_runner.classify_failure(out)
        assert kind != "not_logged_in", "產出的程式碼字串不該被當成 codex 沒登入"

    def test_a_genuine_auth_error_line_still_fires(self):
        """反向保護：真的錯誤還是要抓到，不能為了避免誤判就什麼都不認。"""
        kind, hint = codex_runner.classify_failure(
            "ERROR: You are not logged in. Please run `codex login` to continue.")
        assert kind == "not_logged_in" and "codex login" in hint


class TestOnlyTrulyUnrecoverableSkipsRetries:
    def test_quota_is_retryable(self):
        """速率限制正是 retry-with-backoff 要處理的——判成不可恢復會丟掉重試預算。"""
        assert "quota" not in codex_runner._FATAL_KINDS
        kind, hint = codex_runner.classify_failure(
            'ERROR: 429 rate limit exceeded, please retry later')
        assert kind == "quota"
        assert "重試不會有幫助" not in hint, "訊息不能與實際行為矛盾（它其實會重試）"

    def test_config_problems_are_not_retryable(self):
        assert codex_runner._FATAL_KINDS == {"model_not_supported", "not_logged_in", "untrusted_directory", "invalid_cli_arguments"}


class TestFatalReallyStopsTheRetryLoop:
    """真的跑一次迴圈，不是 grep 原始碼。

    PR 描述宣稱「72 秒縮到 24 秒」，但那是手動觀察到的；沒有這條，`fatal` 的接線
    斷掉也不會有人知道（原本只有比對原始碼字串的測試，改個變數名就失效）。
    """

    # 假 codex：印一段跟真實 400 同形狀的訊息再以 1 離開。用 json.dumps 組，
    # 避免在測試檔裡疊三層引號。
    FAKE_400 = "\n".join([
        "import json, sys",
        "msg = \"The 'gpt-9' model is not supported when using Codex with a ChatGPT account.\"",
        "payload = {'type': 'error', 'status': 400,",
        "           'error': {'type': 'invalid_request_error', 'message': msg}}",
        "sys.stdout.write('ERROR: ' + json.dumps(payload))",
        "sys.exit(1)",
    ])

    def _fake(self, tmp: Path, body: str) -> str:
        s = tmp / "fake_codex.py"
        s.write_text(body, encoding="utf-8")
        return '"' + sys.executable + '" "' + str(s) + '"'

    def test_stops_after_one_attempt_and_reports_fatal(self, tmp_path):
        rc, res = _run(tmp_path, self._fake(tmp_path, self.FAKE_400), "--retries", "3")
        assert res["attempts"] == 1, f"設定問題不該燒掉 3 次重試：{res}"
        assert res["fatal"] is True
        assert "gpt-9" in res["reason"], "要指名是哪個模型"
        assert rc == 1

    def test_ordinary_failure_still_uses_the_whole_retry_budget(self, tmp_path):
        """反向保護：不能把所有失敗都當成 fatal，那會讓重試機制整個失效。"""
        body = "\n".join([
            "import sys",
            "sys.stdout.write('boom, something transient')",
            "sys.exit(1)",
        ])
        _, res = _run(tmp_path, self._fake(tmp_path, body), "--retries", "3")
        assert res["attempts"] == 3, f"一般失敗要照常重試三次：{res}"
        assert res.get("fatal") is False

    def test_failure_output_reaches_the_operator(self, tmp_path):
        """一般失敗也要看得到 codex 到底印了什麼——以前全被 DEVNULL 吃掉。"""
        body = "\n".join([
            "import sys",
            "sys.stdout.write('DISTINCTIVE_MARKER_XYZ')",
            "sys.exit(1)",
        ])
        _, res = _run(tmp_path, self._fake(tmp_path, body), "--retries", "1")
        assert "DISTINCTIVE_MARKER_XYZ" in res["reason"], res["reason"]
