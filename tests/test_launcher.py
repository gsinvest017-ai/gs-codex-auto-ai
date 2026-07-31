"""desktop/launcher.py — 需求清理與啟動指令組裝測試。

重點：launcher 把需求交給終端機執行 `claude "<需求>"`，中間必經一層 shell
（Windows `cmd /k`、Linux `bash -lc`）。原本只把 `"` 換成 `'`，含 `$(...)` /
`` `...` `` / `&` 的需求會被那層 shell 當成指令執行。
"""
import json
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
