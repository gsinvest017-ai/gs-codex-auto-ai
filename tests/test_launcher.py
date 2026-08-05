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



class TestSpecIsTheOnlyEntryPoint:
    """「從 spec 開始」是唯一的啟動入口，而且必須跟內嵌面板走同一條路。

    以前它自己直接呼叫 `launch_claude()`，所以永遠開在**視窗外的原生終端機**——
    跟旁邊那顆「啟動新任務」行為不一致，使用者按了以為會進右邊的面板，結果跳出
    一個 PowerShell 視窗。
    """

    class _UI:
        on_seed_from_spec = launcher.LauncherUI.on_seed_from_spec

        def __init__(self, intent="做個東西"):
            self.started = []
            self._app_run = False

            class _T:
                def get(self, *a):
                    return intent

            class _S:
                def config(self, **kw):
                    pass

            class _R:
                def update_idletasks(self):
                    pass

            self.req, self.status, self.root = _T(), _S(), _R()

        def _start_task(self, req):
            self.started.append(req)

    def test_hands_the_spec_prompt_to_the_shared_launcher(self, monkeypatch):
        monkeypatch.setattr(launcher, "seed_from_spec",
                            lambda i: "依照規格檔 C:/v/spec.md 開發，跑完整七階段")
        ui = self._UI()
        ui.on_seed_from_spec()
        assert ui.started == ["依照規格檔 C:/v/spec.md 開發，跑完整七階段"],             "沒有走共用的啟動路徑（那條才會進內嵌面板）"

    def test_does_not_launch_when_spec_generation_fails(self, monkeypatch):
        monkeypatch.setattr(launcher, "seed_from_spec", lambda i: None)
        ui = self._UI()
        ui.on_seed_from_spec()
        assert ui.started == []
        assert ui._app_run is False, "沒啟動卻上了膛"


class TestStartTaskPrefersTheEmbeddedPanel:
    """`_start_task` 是兩顆按鈕共用的那一段：內嵌優先、不可用才退回外部終端機。"""

    class _UI:
        _start_task = launcher.LauncherUI._start_task

        def __init__(self, embed=True):
            self._app_run = False
            self._app_run_at = 0.0
            self.msgs = []

            class _V:
                def __init__(self, v):
                    self.v = v

                def get(self):
                    return self.v

            class _S:
                def __init__(self, out):
                    self.out = out

                def config(self, **kw):
                    self.out.append(kw.get("text", ""))

            self.autopilot_var = _V(False)
            self.embed_var = _V(embed)
            self.status = _S(self.msgs)

        def show_terminal_pane(self, open_url):
            return True

    def test_uses_the_embedded_panel_when_available(self, monkeypatch, tmp_path):
        monkeypatch.setattr(launcher, "CONFIG_PATH", tmp_path / "c.json")
        launcher.set_project_dir(tmp_path / "p")
        monkeypatch.setattr(launcher.TERMINAL, "available", lambda: (True, ""))
        seen = {}
        monkeypatch.setattr(launcher.TERMINAL, "open",
                            lambda **kw: seen.update(kw) or True)
        monkeypatch.setattr(launcher, "launch_claude",
                            lambda *a, **k: pytest.fail("不該開外部終端機"))
        ui = self._UI()
        ui._start_task("依照規格檔 C:/v/spec.md 開發")
        assert seen.get("kind") == "claude"
        assert "spec.md" in seen.get("prompt", "")
        assert ui._app_run is True, "啟動前要上膛"

    def test_falls_back_to_an_external_terminal(self, monkeypatch, tmp_path):
        monkeypatch.setattr(launcher, "CONFIG_PATH", tmp_path / "c.json")
        launcher.set_project_dir(tmp_path / "p")
        monkeypatch.setattr(launcher.TERMINAL, "available",
                            lambda: (False, "這台 Windows 太舊"))
        called = []
        monkeypatch.setattr(launcher, "launch_claude",
                            lambda req, autopilot=False: called.append(req) or True)
        ui = self._UI()
        ui._start_task("做個東西")
        assert called == ["做個東西"]


def test_switching_project_dir_invalidates_the_cached_root(monkeypatch, tmp_path):
    """換資料夾之後，心跳 / 進度卡 / 中止旗標不能還指著舊的那個。

    `active_project_dir()` 讀的是 `prepare_project_dir()` 快取下來的結果；不作廢
    的話它會跟 session 實際跑的地方分家——守門員在 session 那邊找不到標記就
    fail-open，而且沒有徵兆。
    """
    monkeypatch.setattr(launcher, "CONFIG_PATH", tmp_path / "c.json")
    launcher.set_project_dir(tmp_path / "old")
    launcher.prepare_project_dir()
    assert launcher.active_project_dir() == tmp_path / "old"

    launcher.set_project_dir(tmp_path / "new")
    launcher._RESOLVED_ROOT = None          # on_pick_project 會做的事
    assert launcher.active_project_dir() == tmp_path / "new"


def test_action_buttons_restore_to_their_original_state():
    """`_set_actions_enabled` 會記住原狀態再還原。名單裡若有兩個名字指向**同一顆**
    按鈕，第二次讀到的已經是 disabled，還原後那顆就永遠按不下去了。"""
    names = ("launch_btn", "term_btn", "collapse_btn", "popout_btn")

    class _Btn:
        def __init__(self, state):
            self.state = state

        def cget(self, k):
            return self.state

        def config(self, **kw):
            self.state = kw.get("state", self.state)

    class _UI:
        _set_actions_enabled = launcher.LauncherUI._set_actions_enabled

        def __init__(self):
            self._btn_state = {}
            self.launch_btn = _Btn("disabled")     # 環境沒就緒時本來就是停用的
            self.term_btn = _Btn("normal")
            self.collapse_btn = _Btn("normal")
            self.popout_btn = _Btn("normal")

    ui = _UI()
    ui._set_actions_enabled(False)
    assert all(getattr(ui, n).state == "disabled" for n in names)
    ui._set_actions_enabled(True)
    assert ui.launch_btn.state == "disabled", "環境沒就緒的按鈕不該被還原成可按"
    assert ui.term_btn.state == "normal"


class TestTrustProjectDir:
    """新資料夾第一次開 session 會停在「Yes, I trust this folder」等人按 Enter。
    內嵌面板目前還沒辦法接受鍵盤輸入，所以那一步等於直接卡死。"""

    def test_marks_the_folder_trusted(self, tmp_path, monkeypatch):
        state = tmp_path / ".claude.json"
        state.write_text(json.dumps({"projects": {}}), encoding="utf-8")
        monkeypatch.setattr(launcher, "CLAUDE_STATE", state)
        proj = tmp_path / "proj"
        assert launcher.trust_project_dir(proj) is True
        d = json.loads(state.read_text(encoding="utf-8"))
        key = str(proj).replace("\\", "/")
        assert d["projects"][key]["hasTrustDialogAccepted"] is True

    def test_uses_forward_slashes(self, tmp_path, monkeypatch):
        """Claude Code 的鍵是正斜線格式；用反斜線寫進去等於另開一筆、不會生效。"""
        state = tmp_path / ".claude.json"
        state.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(launcher, "CLAUDE_STATE", state)
        # 用 tmp_path 底下的路徑，不要寫死真實路徑——第一版寫了
        # `C:\Users\User\CodexAutoAI`，那在開發機上是**真的存在**的資料夾，
        # 測試結果會被那台機器當下的狀態左右（後來加了「.claude 是誰的」檢查就紅了）。
        proj = tmp_path / "a" / "b"
        launcher.trust_project_dir(proj)
        d = json.loads(state.read_text(encoding="utf-8"))
        key = str(proj).replace("\\", "/")
        assert key in d["projects"], list(d["projects"])
        assert "\\" not in key

    def test_keeps_other_projects_untouched(self, tmp_path, monkeypatch):
        state = tmp_path / ".claude.json"
        state.write_text(json.dumps({
            "projects": {"C:/別人的專案": {"hasTrustDialogAccepted": False, "x": 1}},
            "其他設定": "保留",
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(launcher, "CLAUDE_STATE", state)
        launcher.trust_project_dir(tmp_path / "mine")
        d = json.loads(state.read_text(encoding="utf-8"))
        assert d["projects"]["C:/別人的專案"] == {"hasTrustDialogAccepted": False, "x": 1}
        assert d["其他設定"] == "保留"

    def test_is_idempotent(self, tmp_path, monkeypatch):
        state = tmp_path / ".claude.json"
        state.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(launcher, "CLAUDE_STATE", state)
        proj = tmp_path / "proj"
        assert launcher.trust_project_dir(proj) is True
        assert launcher.trust_project_dir(proj) is False, "已經信任了不該重寫"

    def test_never_raises_on_broken_state(self, tmp_path, monkeypatch):
        state = tmp_path / ".claude.json"
        state.write_text("{ 這不是 JSON", encoding="utf-8")
        monkeypatch.setattr(launcher, "CLAUDE_STATE", state)
        launcher.trust_project_dir(tmp_path / "proj")     # 不該拋

    def test_does_not_lose_the_file_on_a_failed_write(self, tmp_path, monkeypatch):
        """那個檔有 170 KB 的使用者狀態，寫到一半掛掉會全毀，所以要先寫暫存再換。"""
        state = tmp_path / ".claude.json"
        original = json.dumps({"projects": {"a": {"hasTrustDialogAccepted": True}}})
        state.write_text(original, encoding="utf-8")
        monkeypatch.setattr(launcher, "CLAUDE_STATE", state)
        monkeypatch.setattr(launcher.Path, "replace",
                            lambda self, target: (_ for _ in ()).throw(OSError("boom")))
        launcher.trust_project_dir(tmp_path / "proj")
        assert state.read_text(encoding="utf-8") == original, "原檔被破壞了"

    def test_does_not_trust_a_folder_with_someone_elses_claude_dir(
            self, tmp_path, monkeypatch):
        """`.claude/settings.json` 的 hook 會跑任意 shell 指令，那正是 claude 要跳
        信任確認的原因。使用者指向一個本來就有自己 `.claude/` 的既有專案時，
        那份設定我們一無所知——替他跳過確認等於把同意權拿掉。"""
        state = tmp_path / ".claude.json"
        state.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(launcher, "CLAUDE_STATE", state)
        proj = tmp_path / "theirs"
        (proj / ".claude").mkdir(parents=True)
        (proj / ".claude" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")
        # 戳記說我們沒放過 .claude
        (proj / launcher.STAMP_NAME).write_text(
            json.dumps({"version": "1", "installed": ["tools"]}), encoding="utf-8")
        assert launcher.trust_project_dir(proj) is False
        assert json.loads(state.read_text(encoding="utf-8")).get("projects", {}) == {}

    def test_trusts_when_the_claude_dir_is_ours(self, tmp_path, monkeypatch):
        state = tmp_path / ".claude.json"
        state.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(launcher, "CLAUDE_STATE", state)
        proj = tmp_path / "ours"
        (proj / ".claude").mkdir(parents=True)
        (proj / ".claude" / "settings.json").write_text('{"hooks":{}}', encoding="utf-8")
        (proj / launcher.STAMP_NAME).write_text(
            json.dumps({"version": "1", "installed": [".claude", "tools"]}),
            encoding="utf-8")
        assert launcher.trust_project_dir(proj) is True

    def test_trusts_a_folder_with_no_claude_dir(self, tmp_path, monkeypatch):
        """沒有 .claude/ 就沒有預先核准的 hook 可以跑，信任是安全的。"""
        state = tmp_path / ".claude.json"
        state.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(launcher, "CLAUDE_STATE", state)
        assert launcher.trust_project_dir(tmp_path / "empty") is True


class TestPrimaryButton:
    """`tk.Button` 只吃單一字型，做不出「大標＋小字說明」，暗色主題下自帶的邊框
    也很突兀。改用 Frame + 兩個 Label 自己組，但對外要維持 tk.Button 的介面，
    `refresh()` / `_set_actions_enabled()` 才不用改。"""

    def _btn(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        from tkinter import font as tkfont
        b = launcher.PrimaryButton(root, "啟動新任務", "先產規格 → 七階段自動開發",
                                   lambda: clicks.append(1),
                                   tkfont.Font(size=14), tkfont.Font(size=9))
        return root, b

    def test_state_round_trips_like_tk_button(self):
        global clicks
        clicks = []
        root, b = self._btn()
        try:
            assert b.cget("state") == "normal"
            b.config(state="disabled")
            assert b.cget("state") == "disabled"
            b.config(state="normal")
            assert b.cget("state") == "normal"
        finally:
            root.destroy()

    def test_disabled_button_does_not_fire(self):
        global clicks
        clicks = []
        root, b = self._btn()
        try:
            b.config(state="disabled")
            b._click()
            assert clicks == [], "停用了還是被按下去"
            b.config(state="normal")
            b._click()
            assert clicks == [1]
        finally:
            root.destroy()

    def test_hover_is_ignored_while_disabled(self):
        global clicks
        clicks = []
        root, b = self._btn()
        try:
            b.config(state="disabled")
            off = b.frame.cget("bg")
            b._paint(launcher.GOLD_HOVER)
            assert b.frame.cget("bg") == off, "停用中還會 hover 變色"
        finally:
            root.destroy()


class TestChecksRunOffTheUiThread:
    """`gather_checks()` 會叫起好幾個子行程，每個都有秒級逾時。以前在 `__init__`
    同步跑完才畫 UI，App 開起來要卡好幾秒才點得到輸入框。"""

    class _UI:
        _apply_checks = launcher.LauncherUI._apply_checks
        _poll_checks = launcher.LauncherUI._poll_checks

        def __init__(self):
            self._checking = True
            self.rows = {}
            self.msgs = []
            self.after_calls = []
            self.btn_state = []

            class _S:
                def __init__(self, out):
                    self.out = out

                def config(self, **kw):
                    self.out.append(kw.get("text", ""))

            class _B:
                def __init__(self, out):
                    self.out = out

                def config(self, **kw):
                    self.out.append(kw.get("state"))

            class _R:
                def __init__(self, out):
                    self.out = out

                def after(self, ms, fn):
                    self.out.append(ms)

            self.status = _S(self.msgs)
            self.launch_btn = _B(self.btn_state)
            self.root = _R(self.after_calls)

    def test_polls_again_while_the_worker_is_still_running(self):
        ui = self._UI()
        ui._checks_result = None
        ui._poll_checks()
        assert ui.after_calls, "結果還沒好就該再排一次輪詢"
        assert ui._checking is True

    def test_applies_the_result_once_it_arrives(self):
        ui = self._UI()
        ui._checks_result = [{"key": "claude", "ok": True, "msg": "ok", "critical": True}]
        ui._poll_checks()
        assert ui._checking is False
        assert ui.btn_state == ["normal"]

    def test_a_failed_check_run_does_not_leave_it_stuck(self):
        """worker 掛掉會回空 list——不能就讓 UI 永遠停在「檢查中…」。"""
        ui = self._UI()
        ui._checks_result = []
        ui._poll_checks()
        assert ui._checking is False
        assert any("重新檢查" in m for m in ui.msgs)
