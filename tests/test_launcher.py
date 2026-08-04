"""desktop/launcher.py — 需求清理與啟動指令組裝測試。

重點：launcher 把需求交給終端機執行 `claude "<需求>"`，中間必經一層 shell
（Windows `cmd /k`、Linux `bash -lc`）。原本只把 `"` 換成 `'`，含 `$(...)` /
`` `...` `` / `&` 的需求會被那層 shell 當成指令執行。
"""
import json
import os
import time
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

launcher = pytest.importorskip("launcher")

# 任何輸出都不該再含這些字元
SHELL_META = set('"\'`$&|;<>^%!\\\n\r\t')

# JS / Python 兩邊都要跑的同一組案例（順序有意義，用於 parity 比對）
CASES = [
    "做一個記帳 CLI 工具，資料存 SQLite",
    "做一個 $(rm -rf ~) 工具",
    "做一個 `whoami` 工具",
    "做 A & calc.exe",
    "做 A | more",
    '說 "hello" 給我',
    "多行\n需求\t內容",
    "做一個工具（含中文全形括號）與「引號」",
    "報表 100% 完成，很棒!",
    "價格 $100 以下 & 交期 <7 天",
    "a;b>c<d^e\\f'g",
]


# ── 清理規則 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", CASES)
def test_output_never_contains_shell_metacharacters(text):
    out = launcher._safe_prompt(text)
    assert not (set(out) & SHELL_META), f"殘留語法字元：{set(out) & SHELL_META}"


def test_command_substitution_is_defused():
    assert launcher._safe_prompt("做一個 $(rm -rf ~) 工具") == "做一個 ＄(rm -rf ~) 工具"


def test_backtick_removed():
    assert launcher._safe_prompt("做一個 `whoami` 工具") == "做一個 whoami 工具"


def test_command_chaining_defused():
    assert launcher._safe_prompt("做 A & calc.exe") == "做 A ＆ calc.exe"
    assert launcher._safe_prompt("做 A | more") == "做 A more"


def test_chinese_and_fullwidth_punctuation_untouched():
    text = "做一個工具（含中文全形括號）與「引號」，資料存 SQLite"
    assert launcher._safe_prompt(text) == text


def test_prose_meaning_preserved_via_fullwidth():
    """`100%` / `很棒!` 這類 prose 不該被刪成 `100` / `很棒`。"""
    assert launcher._safe_prompt("報表 100% 完成，很棒!") == "報表 100％ 完成，很棒！"


def test_newlines_folded_to_single_spaces():
    assert launcher._safe_prompt("多行\n\n需求\t內容") == "多行 需求 內容"


def test_empty_and_none_are_safe():
    assert launcher._safe_prompt("") == ""
    assert launcher._safe_prompt(None) == ""
    assert launcher._safe_prompt("   \n\t  ") == ""


# ── 啟動指令組裝：一律 argv list、絕不 shell=True ──────────────────────────
class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return object()


@pytest.fixture
def spy(monkeypatch):
    s = _Spy()
    monkeypatch.setattr(launcher.subprocess, "Popen", s)
    monkeypatch.setattr(launcher, "_which", lambda n: f"/usr/bin/{n}")
    return s


def test_never_uses_shell_true(spy, monkeypatch):
    """shell=True + 字串拼接是原本的漏洞來源，不該再出現。"""
    for win in (True, False):
        spy.calls.clear()
        monkeypatch.setattr(launcher, "IS_WIN", win)
        assert launcher.launch_claude("做一個 CLI 工具") is True
        assert spy.calls, "沒有啟動任何行程"
        for args, kwargs in spy.calls:
            assert kwargs.get("shell") is not True, f"仍在用 shell=True（IS_WIN={win}）"
            assert isinstance(args[0], list), f"argv 不是 list（IS_WIN={win}）：{args[0]!r}"


def test_posix_passes_requirement_as_bash_positional_param(spy, monkeypatch):
    """需求以 $2 傳入，bash 從頭到尾不會把它當程式碼解析。"""
    monkeypatch.setattr(launcher, "IS_WIN", False)
    launcher.launch_claude("做一個 CLI 工具")
    argv = spy.calls[0][0][0]
    joined = " ".join(argv)
    assert '"$2"' in joined, argv          # 需求走位置參數
    assert "做一個 CLI 工具" in argv         # 且原文完整出現在 argv 而非嵌進腳本
    script_idx = argv.index('cd "$1" && exec claude ${2:+"$2"}')
    assert argv[script_idx + 1] == "bash"  # $0
    assert argv[script_idx + 3] == "做一個 CLI 工具"   # $2


def test_windows_uses_argv_list_for_cmd(spy, monkeypatch):
    monkeypatch.setattr(launcher, "IS_WIN", True)
    launcher.launch_claude("做一個 CLI 工具")
    argv = spy.calls[0][0][0]
    assert "claude" in argv
    assert "做一個 CLI 工具" in argv        # 當成獨立 argv 元素，不是拼進字串


def test_autopilot_prefix_applied(spy, monkeypatch):
    monkeypatch.setattr(launcher, "IS_WIN", True)
    launcher.launch_claude("做一個 CLI 工具", autopilot=True)
    argv = spy.calls[0][0][0]
    assert "/autopilot on 做一個 CLI 工具" in argv


def test_empty_requirement_launches_bare_claude(spy, monkeypatch):
    monkeypatch.setattr(launcher, "IS_WIN", True)
    launcher.launch_claude("")
    argv = spy.calls[0][0][0]
    assert argv[-1] == "claude"            # 沒有空字串 prompt 尾巴


def test_missing_claude_binary_refuses(monkeypatch):
    monkeypatch.setattr(launcher, "_which", lambda n: None)
    shown = []
    monkeypatch.setattr(launcher.messagebox, "showerror", lambda *a: shown.append(a))
    assert launcher.launch_claude("任何需求") is False
    assert shown


# ── JS / Python 規則一致性（同一句需求，兩個 UI 結果必須相同）──────────────
@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能比對 JS 版")
def test_js_and_python_sanitisers_agree(tmp_path):
    script = tmp_path / "run.js"
    prompt_js = (ROOT / "vscode-extension" / "prompt.js").as_posix()
    script.write_text(
        f"const {{ safePrompt }} = require({json.dumps(prompt_js)});\n"
        f"const cases = {json.dumps(CASES, ensure_ascii=False)};\n"
        "process.stdout.write(JSON.stringify(cases.map(safePrompt)));\n",
        encoding="utf-8")
    proc = subprocess.run([shutil.which("node"), str(script)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    js = json.loads(proc.stdout)
    py = [launcher._safe_prompt(c) for c in CASES]
    assert js == py, f"兩邊規則漂移了：\njs={js}\npy={py}"


# ── 內嵌終端機的調度分支 ─────────────────────────────────────────────────────
class _FakeSrv:
    """假的 TerminalServer：只要能發 handoff 與回 url 就夠了。"""

    def __init__(self):
        self.handoff_calls = 0
        self.url = "http://127.0.0.1:1/?token=REAL"

    @property
    def handoff_url(self):
        self.handoff_calls += 1
        return f"http://127.0.0.1:1/?handoff=N{self.handoff_calls}"


def _term(monkeypatch, srv):
    """一個接上假 server 的 EmbeddedTerminal（不起真的服務、不生行程）。"""
    t = launcher.EmbeddedTerminal()
    monkeypatch.setattr(t, "_ensure", lambda: srv)
    shown = []
    monkeypatch.setattr(t, "_show", lambda url: shown.append(url))
    return t, shown


class TestEmbeddedTerminalDispatch:
    """`open()` 的 shell 分支。**真 token 不能外流到會變成命令列參數的地方**，
    而「內嵌成功了還是又開了一個瀏覽器」則是使用者一眼就看得出來的行為差異。"""

    def test_successful_shell_does_not_also_open_a_window(self, monkeypatch):
        srv = _FakeSrv()
        term, shown = _term(monkeypatch, srv)
        term.open(shell=lambda open_url: True)
        assert shown == [], "內嵌成功了還去開獨立視窗＝使用者會看到兩個終端機"

    def test_shell_returning_false_falls_back_to_a_window(self, monkeypatch):
        srv = _FakeSrv()
        term, shown = _term(monkeypatch, srv)
        term.open(shell=lambda open_url: False)
        assert len(shown) == 1

    def test_shell_blowing_up_still_falls_back(self, monkeypatch):
        """內嵌只是體驗升級——它壞掉不該讓使用者完全開不了終端機。"""
        srv = _FakeSrv()
        term, shown = _term(monkeypatch, srv)

        def boom(open_url):
            raise RuntimeError("內嵌炸了")

        term.open(shell=boom)
        assert len(shown) == 1

    def test_window_fallback_never_gets_the_real_token(self, monkeypatch):
        """`_show()` 的兩條路（pywebview / webbrowser）都可能變成另一個行程的
        命令列參數，所以它拿到的必須是 handoff 券而不是 token。"""
        srv = _FakeSrv()
        term, shown = _term(monkeypatch, srv)
        term.open()
        assert "token=REAL" not in shown[0]
        assert "handoff=" in shown[0]

    def test_handoff_is_minted_lazily(self, monkeypatch):
        """shell 沒去要 URL（面板已經嵌著、只是重新展開）就不該白發一張券。"""
        srv = _FakeSrv()
        term, _ = _term(monkeypatch, srv)
        term.open(shell=lambda open_url: True)          # 不呼叫 open_url
        assert srv.handoff_calls == 0
        term.open(shell=lambda open_url: bool(open_url()))
        assert srv.handoff_calls == 1


class TestShutdownReaping:
    """關 App 一定要收乾淨——這個 repo 在 pty 那邊已經為孤兒行程吃過苦頭。"""

    def test_shutdown_runs_registered_closers(self, monkeypatch):
        term, _ = _term(monkeypatch, _FakeSrv())
        calls = []
        term.add_closer(lambda: calls.append("closed"))
        term.shutdown()
        assert calls == ["closed"]

    def test_one_closer_blowing_up_does_not_skip_the_others(self, monkeypatch):
        term, _ = _term(monkeypatch, _FakeSrv())
        calls = []

        def boom():
            raise RuntimeError("收尾炸了")

        term.add_closer(boom)
        term.add_closer(lambda: calls.append("second"))
        term.shutdown()
        assert calls == ["second"], "第一個收尾失敗就漏掉後面的＝留下孤兒行程"

    def test_closers_run_only_once(self, monkeypatch):
        """關閉路徑有兩個入口（WM_DELETE_WINDOW 與 atexit），兩邊都會呼叫。"""
        term, _ = _term(monkeypatch, _FakeSrv())
        calls = []
        term.add_closer(lambda: calls.append(1))
        term.shutdown()
        term.shutdown()
        assert calls == [1]


class TestOpenDoesNotHandBackTheToken:
    """`open()` 的用途常是「給我一個能丟給外面開的 URL」，所以它不該回傳帶真
    token 的那一個——那是等著被下一個呼叫者踩的坑。"""

    def test_open_reports_success_not_a_url(self, monkeypatch):
        """回 bool 而不是 URL：真 token 不該被交出去，但呼叫端要知道成不成功。"""
        srv = _FakeSrv()
        term, _ = _term(monkeypatch, srv)
        assert term.open(shell=lambda open_url: True) is True
        assert term.open() is True

    def test_open_reports_failure_when_the_session_cannot_start(self, monkeypatch):
        """以前失敗只跳 messagebox 就正常返回，呼叫端無從得知，於是照樣回報
        「已開啟 Claude 分頁」、守門員也一直上著膛，實際上一個 session 都沒有。"""
        srv = _FakeSrv()
        term, shown = _term(monkeypatch, srv)

        class _Mgr:
            def create(self, **kw):
                raise RuntimeError("同時開啟的 session 已達上限 12")

        term._mgr = _Mgr()
        monkeypatch.setattr(launcher.messagebox, "showerror", lambda *a, **k: None)
        assert term.open(kind="claude", shell=lambda open_url: True) is False
        assert shown == [], "建 session 失敗就不該再去開視窗"


class TestEmbedDiagnosticLog:
    """內嵌失敗的原因要留得下來。

    狀態列那行是**瞬間**的——使用者回報「跳出兩個視窗」時往往已經看不到原因，
    只能靠猜（這次就猜了很久）。所以另外寫一份到固定位置。
    """

    def test_appends_with_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher.tempfile, "gettempdir", lambda: str(tmp_path),
                            raising=False)
        launcher._note_embed("失敗：找不到視窗")
        launcher._note_embed("失敗：第二次")
        log = tmp_path / "codexautoai-embed.log"
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2, "要用附加的，不能把上一次蓋掉"
        assert "找不到視窗" in lines[0] and "第二次" in lines[1]
        assert lines[0][:4].isdigit(), f"每行要有時間戳：{lines[0]!r}"

    def test_never_raises(self, monkeypatch):
        """診斷紀錄自己不該變成新的故障點（例如目錄唯讀）。"""
        def boom():
            raise OSError("寫不進去")
        monkeypatch.setattr(launcher.tempfile, "gettempdir", boom, raising=False)
        launcher._note_embed("whatever")      # 不該拋

    def test_caps_the_file(self, tmp_path, monkeypatch):
        """沒有輪替的話診斷檔會隨 App 壽命無限長大，而要看的只有最近幾次。"""
        monkeypatch.setattr(launcher.tempfile, "gettempdir", lambda: str(tmp_path),
                            raising=False)
        monkeypatch.setattr(launcher, "_EMBED_LOG_LINES", 5)
        for i in range(20):
            launcher._note_embed(f"第 {i} 次")
        lines = (tmp_path / "codexautoai-embed.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 5, f"沒有截斷：{len(lines)} 行"
        assert "第 19 次" in lines[-1], "留錯了，該留最後幾行"

    def test_cap_of_one_keeps_exactly_one_line(self, tmp_path, monkeypatch):
        """上限是 1 時 `-(1-1)` 會變成 `-0 == 0`，`old[0:]` 整份留下來
        ——截斷會安靜地失效。"""
        monkeypatch.setattr(launcher.tempfile, "gettempdir", lambda: str(tmp_path),
                            raising=False)
        monkeypatch.setattr(launcher, "_EMBED_LOG_LINES", 1)
        for i in range(5):
            launcher._note_embed(f"第 {i} 次")
        lines = (tmp_path / "codexautoai-embed.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1 and "第 4 次" in lines[0]


class TestPythonCheck:
    """凍結版以前直接回報「內建於 App」就過關——那是錯的，而且錯得很安靜。

    exe 嵌的 runtime 只給 App 自己用；`.claude/settings.json` 的三個 hook 都是
    `python "$CLAUDE_PROJECT_DIR/tools/…"`，另外開行程、吃 PATH。沒有它的後果
    不是「某個功能不能用」，而是 Codex-first 守門員、進度、autopilot 全部靜默
    失效——pipeline 照跑，但核心不變式沒人守。
    """

    def test_reports_missing_python(self, monkeypatch):
        monkeypatch.setattr(launcher, "_which", lambda n: None)
        ok, msg = launcher.check_python()
        # 括號不能省：`a and b or c` 會 parse 成 `(a and b) or c`，而訊息裡本來就有
        # "hooks"，右邊恆真——那樣寫的話 ok=True 也照樣通過，等於沒驗到。
        assert ok is False and ("hook" in msg.lower() or "hooks" in msg)

    def test_rejects_too_old(self, monkeypatch):
        monkeypatch.setattr(launcher, "_which", lambda n: r"C:\py\python.exe")
        monkeypatch.setattr(launcher, "_run", lambda cmd, timeout=10: (0, "3 8"))
        ok, msg = launcher.check_python()
        assert ok is False and "3.8" in msg

    def test_old_python_but_new_python3(self, monkeypatch):
        """Linux 上 `python` 常常是舊的、能用的是 `python3`。

        這項是 critical，所以「看到舊的就放棄」會讓一台其實裝了 3.12 的機器
        按不下「啟動」。
        """
        monkeypatch.setattr(launcher, "_which", lambda n: f"/usr/bin/{n}")

        def run(cmd, timeout=10):
            # 路徑在指令裡是**帶引號**的，比對要連引號一起看——否則
            # `/usr/bin/python` 這個前綴也會命中 `/usr/bin/python3`，兩個候選
            # 拿到同一個版本，測試就變成永遠會過（第一版就是這樣）。
            return (0, "3 8") if '"/usr/bin/python"' in cmd else (0, "3 12")

        monkeypatch.setattr(launcher, "_run", run)
        ok, msg = launcher.check_python()
        assert ok is True, f"有可用的 python3 卻回報不可用：{msg}"
        assert "3.12" in msg

    def test_all_candidates_too_old_reports_the_version(self, monkeypatch):
        monkeypatch.setattr(launcher, "_which", lambda n: f"/usr/bin/{n}")
        monkeypatch.setattr(launcher, "_run", lambda cmd, timeout=10: (0, "3 8"))
        ok, msg = launcher.check_python()
        assert ok is False and "3.8" in msg, msg

    def test_accepts_new_enough(self, monkeypatch):
        monkeypatch.setattr(launcher, "_which", lambda n: r"C:\py\python.exe")
        monkeypatch.setattr(launcher, "_run", lambda cmd, timeout=10: (0, "3 12"))
        ok, msg = launcher.check_python()
        assert ok is True and "3.12" in msg

    def test_falls_back_to_python3(self, monkeypatch):
        seen = []

        def which(n):
            seen.append(n)
            return "/usr/bin/python3" if n == "python3" else None

        monkeypatch.setattr(launcher, "_which", which)
        monkeypatch.setattr(launcher, "_run", lambda cmd, timeout=10: (0, "3 11"))
        ok, _ = launcher.check_python()
        assert ok is True and seen == ["python", "python3"]

    def test_version_probe_survives_banner_noise(self, monkeypatch):
        """`_run` 把 stdout 與 stderr 串在一起，pyenv / sitecustomize 的 banner 會
        混進來。只看最後一行的話一個 banner 就讓這道 critical 檢查靜默降級。"""
        monkeypatch.setattr(launcher, "_which", lambda n: "/usr/bin/python")
        monkeypatch.setattr(launcher, "_run",
                            lambda cmd, timeout=10: (0, "3 12\nsome pyenv banner\n"))
        ok, msg = launcher.check_python()
        assert ok is True and "3.12" in msg, msg

    def test_python_is_critical(self, monkeypatch):
        """不是 critical 的話環境檢查照樣全綠、「啟動」按得下去——等於白檢查。"""
        monkeypatch.setattr(launcher, "check_claude", lambda: (True, ""))
        monkeypatch.setattr(launcher, "check_codex", lambda: (True, ""))
        monkeypatch.setattr(launcher, "check_gh", lambda: (True, ""))
        monkeypatch.setattr(launcher, "check_simple", lambda n, l: (True, ""))
        monkeypatch.setattr(launcher, "check_python", lambda: (False, "沒裝"))
        row = next(c for c in launcher.gather_checks() if c["key"] == "python")
        assert row["critical"] is True and row["ok"] is False


class TestProjectDir:
    """以前所有 session 都跑在安裝目錄，使用者的產出被寫進 App 自己的安裝路徑，
    而且每個任務共用同一份 log/（上一輪的 Phase 進度會被下一輪看到）。"""

    def test_default_is_not_the_install_dir(self):
        assert launcher.default_project_dir() != launcher.APP_DIR

    def test_round_trips_through_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "CONFIG_PATH", tmp_path / "desktop.json")
        target = tmp_path / "我的專案"
        launcher.set_project_dir(target)
        assert launcher.project_dir() == target

    def test_broken_config_falls_back_to_default(self, tmp_path, monkeypatch):
        cfg = tmp_path / "desktop.json"
        cfg.write_text("{ 這不是 JSON", encoding="utf-8")
        monkeypatch.setattr(launcher, "CONFIG_PATH", cfg)
        assert launcher.project_dir() == launcher.default_project_dir()

    def test_app_run_marker_is_written_and_refreshed(self, tmp_path):
        launcher.touch_app_run(tmp_path, prompt="做一個記帳 CLI")
        f = tmp_path / "log" / "app-run.json"
        first = json.loads(f.read_text(encoding="utf-8"))
        assert first["prompt"] == "做一個記帳 CLI" and first["updated_at"] > 0
        time.sleep(0.01)
        launcher.touch_app_run(tmp_path)          # 心跳：不帶 prompt
        second = json.loads(f.read_text(encoding="utf-8"))
        assert second["updated_at"] > first["updated_at"]
        assert second["prompt"] == "做一個記帳 CLI", "心跳不該把原本的需求洗掉"

    def test_touch_never_raises(self, tmp_path):
        """寫不進去只是少一層保護，不該擋住任務啟動。"""
        launcher.touch_app_run(tmp_path / "不存在" / "而且不可寫" / "\x00")


class TestArmingDisarms:
    """守門員上膛之後也要收得回來——不然交付之後只要 App 還開著，Claude 連手動
    改一行都會被擋，而且沒有任何徵兆（跟 state.json 那條路的語意也不一致）。"""

    class _UI:
        """只借 LauncherUI 的兩個方法，不建真的 Tk 視窗。"""

        def __init__(self, log: Path, started_at: float):
            self._app_run = True
            self._app_run_at = started_at
            self._log = log

        _run_finished = launcher.LauncherUI._run_finished

        def _events_log(self):
            return self._log

    def _ui(self, tmp_path, state, log_mtime_offset, started_offset=0.0):
        log = tmp_path / "events.jsonl"
        log.write_text("{}", encoding="utf-8")
        now = time.time()
        os.utime(log, (now + log_mtime_offset, now + log_mtime_offset))
        ui = self._UI(log, now + started_offset)
        return ui, {"state": state}

    def test_disarms_when_this_run_finished(self, tmp_path):
        ui, model = self._ui(tmp_path, "done", log_mtime_offset=+5)
        assert ui._run_finished(model) is True

    def test_stays_armed_while_running(self, tmp_path):
        ui, model = self._ui(tmp_path, "running", log_mtime_offset=+5)
        assert ui._run_finished(model) is False

    def test_stale_log_from_a_previous_run_does_not_disarm(self, tmp_path):
        """剛按下啟動時 events.jsonl 還是上一輪的，狀態多半已是 done——
        只看 state 的話下一次輪詢就收膛，保護在最需要的時候消失。"""
        ui, model = self._ui(tmp_path, "done", log_mtime_offset=-60)
        assert ui._run_finished(model) is False

    def test_missing_model_keeps_it_armed(self, tmp_path):
        ui, _ = self._ui(tmp_path, "done", log_mtime_offset=+5)
        assert ui._run_finished(None) is False


class TestExternalTerminalUsesProjectDir:
    """外部終端機這條路以前寫死安裝目錄。只要使用者取消勾選內嵌、或這台機器
    PTY 不可用，產出就照樣落回 App 的安裝目錄——修了一半等於沒修。"""

    def _spy(self, monkeypatch, tmp_path):
        monkeypatch.setattr(launcher, "CONFIG_PATH", tmp_path / "c.json")
        launcher.set_project_dir(tmp_path / "myproj")
        monkeypatch.setattr(launcher, "_which",
                            lambda n: f"/usr/bin/{n}" if n in ("claude", "wt", "xterm") else None)
        calls = []
        monkeypatch.setattr(launcher.subprocess, "Popen",
                            lambda argv, **kw: calls.append((argv, kw)) or object())
        return calls

    def test_windows_terminal_opens_in_project_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(launcher, "IS_WIN", True)
        calls = self._spy(monkeypatch, tmp_path)
        assert launcher.launch_claude("做個東西") is True
        argv = calls[0][0]
        assert str(tmp_path / "myproj") in argv, argv
        assert str(launcher.APP_DIR) not in argv

    def test_posix_terminal_opens_in_project_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(launcher, "IS_WIN", False)
        calls = self._spy(monkeypatch, tmp_path)
        assert launcher.launch_claude("做個東西") is True
        argv = calls[0][0]
        assert str(tmp_path / "myproj") in argv, argv


class TestFrameworkBootstrap:
    """把 session 的 cwd 從安裝目錄搬到使用者的專案資料夾之後，那個資料夾**必須**
    也有框架檔——沒有 `CLAUDE.md` 就沒有 dispatcher（七階段不會跑），沒有
    `.claude/settings.json` 就一個 hook 都不會載入（Codex-first 守門員、進度、
    autopilot 全部不存在）。等於這次修法把自己要保護的東西關掉了。
    """

    def _fake_app(self, monkeypatch, tmp_path):
        app = tmp_path / "app"
        (app / ".claude").mkdir(parents=True)
        (app / ".claude" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")
        (app / "tools").mkdir()
        (app / "tools" / "enforce_build_codex.py").write_text("# x", encoding="utf-8")
        (app / "CLAUDE.md").write_text("# dispatcher", encoding="utf-8")
        (app / "desktop").mkdir()
        (app / "desktop" / "VERSION").write_text("9.9.9", encoding="utf-8")
        monkeypatch.setattr(launcher, "APP_DIR", app)
        return app

    def test_fresh_project_dir_gets_the_framework(self, monkeypatch, tmp_path):
        self._fake_app(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        launcher.bootstrap_project(proj)
        assert (proj / ".claude" / "settings.json").exists(), "沒有 hooks＝守門員不存在"
        assert (proj / "CLAUDE.md").exists(), "沒有 CLAUDE.md＝dispatcher 不存在"
        assert (proj / "tools" / "enforce_build_codex.py").exists()

    def test_does_not_clobber_on_every_launch(self, monkeypatch, tmp_path):
        """版本沒變就別一直覆蓋——使用者可能在專案裡改過東西。"""
        self._fake_app(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        launcher.bootstrap_project(proj)
        (proj / "CLAUDE.md").write_text("# 我改過的", encoding="utf-8")
        assert launcher.bootstrap_project(proj) == []
        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == "# 我改過的"

    def test_refreshes_when_app_version_changes(self, monkeypatch, tmp_path):
        """App 升級後框架檔要跟上——留著舊的比沒有更難查（指示與行為對不上）。"""
        app = self._fake_app(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        launcher.bootstrap_project(proj)
        (app / "CLAUDE.md").write_text("# 新版 dispatcher", encoding="utf-8")
        (app / "desktop" / "VERSION").write_text("9.9.10", encoding="utf-8")
        assert "CLAUDE.md" in launcher.bootstrap_project(proj)
        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == "# 新版 dispatcher"

    def test_prepare_returns_a_usable_dir(self, monkeypatch, tmp_path):
        self._fake_app(monkeypatch, tmp_path)
        monkeypatch.setattr(launcher, "CONFIG_PATH", tmp_path / "c.json")
        launcher.set_project_dir(tmp_path / "proj")
        root = launcher.prepare_project_dir()
        assert root == tmp_path / "proj"
        assert (root / ".claude" / "settings.json").exists()

    def test_never_raises(self, monkeypatch, tmp_path):
        self._fake_app(monkeypatch, tmp_path)
        launcher.bootstrap_project(Path("\x00不可能建出來的路徑"))

    def test_first_adoption_never_clobbers_an_existing_project(
            self, monkeypatch, tmp_path):
        """使用者很自然會直接指向自己現有的專案，而那裡可能已經有他自己的
        `CLAUDE.md` / `.claude/`——同名就蓋掉是無聲的資料破壞。"""
        self._fake_app(monkeypatch, tmp_path)
        proj = tmp_path / "existing"
        (proj / ".claude").mkdir(parents=True)
        (proj / ".claude" / "settings.json").write_text("我自己的設定", encoding="utf-8")
        (proj / "CLAUDE.md").write_text("我自己的專案規則", encoding="utf-8")

        launcher.bootstrap_project(proj)

        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == "我自己的專案規則"
        assert (proj / ".claude" / "settings.json").read_text(encoding="utf-8") == "我自己的設定"
        # 沒有的那些還是要補進去，否則等於什麼都沒做
        assert (proj / "tools" / "enforce_build_codex.py").exists()

    def test_resolved_root_is_what_the_session_actually_uses(
            self, monkeypatch, tmp_path):
        """建不出目錄時 `prepare_project_dir()` 會退回安裝目錄；心跳與進度卡若還
        看 `project_dir()`，就會跟 session 跑的地方不一致——守門員在 session 那邊
        找不到標記就 fail-open，而且沒有徵兆。"""
        app = self._fake_app(monkeypatch, tmp_path)
        monkeypatch.setattr(launcher, "CONFIG_PATH", tmp_path / "c.json")
        launcher.set_project_dir(tmp_path / "nope")
        monkeypatch.setattr(launcher.Path, "mkdir",
                            lambda self, **kw: (_ for _ in ()).throw(OSError("唯讀")))
        root = launcher.prepare_project_dir()
        assert root == app
        assert launcher.active_project_dir() == app

    def test_upgrade_does_not_clobber_files_we_never_installed(
            self, monkeypatch, tmp_path):
        """保護不能只擋第一次。

        使用者自己的 `CLAUDE.md` 在第一次接管時被正確保留了，但如果戳記只記版本，
        下一次 App 升級（版本變了）就會把它一起蓋掉。
        """
        app = self._fake_app(monkeypatch, tmp_path)
        proj = tmp_path / "existing"
        proj.mkdir()
        (proj / "CLAUDE.md").write_text("我自己的專案規則", encoding="utf-8")

        launcher.bootstrap_project(proj)                 # 第一次接管：保留
        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == "我自己的專案規則"
        assert (proj / "tools" / "enforce_build_codex.py").exists()

        # App 升級
        (app / "desktop" / "VERSION").write_text("9.9.10", encoding="utf-8")
        (app / "CLAUDE.md").write_text("# 新版 dispatcher", encoding="utf-8")
        (app / "tools" / "enforce_build_codex.py").write_text("# 新版", encoding="utf-8")
        launcher.bootstrap_project(proj)

        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == "我自己的專案規則", \
            "升級把使用者自己的檔蓋掉了"
        assert (proj / "tools" / "enforce_build_codex.py").read_text(encoding="utf-8") == "# 新版", \
            "我們自己放的框架檔升級時該跟上"

    def test_legacy_plain_version_stamp_is_treated_conservatively(
            self, monkeypatch, tmp_path):
        """舊格式的戳記（純版本字串）讀不出 installed，保守起見一律不覆蓋。"""
        app = self._fake_app(monkeypatch, tmp_path)
        proj = tmp_path / "old"
        proj.mkdir()
        (proj / launcher.STAMP_NAME).write_text("9.9.8", encoding="utf-8")
        (proj / "CLAUDE.md").write_text("既有內容", encoding="utf-8")
        launcher.bootstrap_project(proj)
        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == "既有內容"

