"""installer/plugin-bootstrap — Codex plugin 一鍵安裝執行檔的靜態守衛。

這裡驗的不是「安裝會不會成功」（那要真的一台機器，見 TEST-PLAN.md 的手動劇本），
而是**建置產物本身的三個必要條件**。這三條都是實際做這支安裝檔時踩到的：

1. `.ps1` 沒有 UTF-8 BOM → Windows PowerShell 5.1 把繁體中文讀成亂碼。而這支腳本
   的目標機器正是「只有內建 PowerShell」的同事電腦。
2. `postinstall` 配 `deleteafterinstall` → Inno 在使用者按「完成」之前就把 {tmp}
   的腳本刪了，按下去只會得到「找不到檔案」。
3. `[Run]` 的續行寫成字面 `\\n` → ISCC 仍然編得過，產出的 .exe 卻不會跑腳本，
   使用者看到一個「安裝成功」但什麼都沒發生的殼。

三種都不會讓編譯失敗，只會讓**使用者拿到壞掉的安裝檔**——所以只能靠測試擋。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

BOOTSTRAP = Path(__file__).resolve().parents[1] / "installer" / "plugin-bootstrap"
PS1 = BOOTSTRAP / "Install-CodexPlugin.ps1"
ISS = BOOTSTRAP / "setup.iss"
BUILD = BOOTSTRAP / "build.ps1"


def _text(p: Path) -> str:
    return p.read_bytes().decode("utf-8-sig")


def _directives(p: Path) -> str:
    """去掉 `;` 註解行的 .iss 內容。

    必要性是實測出來的：檔案裡有一段註解在解釋「為什麼**不**用 postinstall」，
    純字串比對會把那段解釋本身當成違規。要驗的是指令，不是散文。
    """
    return "\n".join(ln for ln in _text(p).splitlines()
                     if not ln.lstrip().startswith(";"))


class TestUtf8Bom:
    """PowerShell 5.1 沒有 BOM 就不會當成 UTF-8——繁體中文直接變亂碼。"""

    @pytest.mark.parametrize("path", [PS1, BUILD], ids=lambda p: p.name)
    def test_has_bom(self, path: Path):
        assert path.read_bytes()[:3] == b"\xef\xbb\xbf", (
            f"{path.name} 缺少 UTF-8 BOM，PowerShell 5.1 會顯示亂碼")

    @pytest.mark.parametrize("path", [PS1, BUILD], ids=lambda p: p.name)
    def test_actually_contains_cjk(self, path: Path):
        """反向保護：若哪天內容變成全英文，上面那條就沒有意義了，要知道。"""
        assert re.search(r"[一-鿿]", _text(path)), (
            f"{path.name} 已無中文，BOM 要求可以放寬——請一併檢視這組測試")


class TestInnoRunEntry:
    """`[Run]` 這段錯了不會編譯失敗，只會讓 .exe 靜靜地什麼都不做。"""

    def test_run_line_is_a_real_continuation_not_literal_backslash_n(self):
        """字面的 `\\n` 曾經被寫進去過（字串處理失誤），ISCC 照樣編得過。"""
        assert "\\n  " not in _text(ISS), (
            r"setup.iss 出現字面的 \n——續行要用行尾反斜線加真正的換行")

    def test_run_invokes_the_script(self):
        run = _directives(ISS).split("[Run]", 1)[1]
        assert "powershell.exe" in run
        assert "Install-CodexPlugin.ps1" in run

    def test_uses_builtin_powershell_not_pwsh(self):
        """內部機器不一定裝了 PowerShell 7；pwsh 會直接找不到。"""
        run = _directives(ISS).split("[Run]", 1)[1]
        assert not re.search(r'Filename:\s*"pwsh', run), "要用 Windows 內建的 powershell.exe"

    def test_execution_policy_is_bypassed(self):
        """從安裝檔解壓出來的未簽章腳本，預設 ExecutionPolicy 會擋掉。"""
        assert "-ExecutionPolicy Bypass" in _directives(ISS)

    def test_runs_as_current_user(self):
        """plugin 裝進 ~/.claude；提權會裝到別的使用者底下，等於沒裝。"""
        assert "runascurrentuser" in _directives(ISS)


def test_postinstall_and_deleteafterinstall_are_not_combined():
    """兩個旗標湊在一起 = 使用者按「完成」時腳本已經被刪掉了。

    Inno 的 deleteafterinstall 在安裝結束時清理 {tmp}，而 postinstall 是**之後**
    才由使用者觸發。所以腳本要嘛在安裝階段跑（現在的做法），要嘛就不能放 {tmp}。
    """
    t = _directives(ISS)
    if "deleteafterinstall" in t:
        run = t.split("[Run]", 1)[1]
        assert "postinstall" not in run, (
            "deleteafterinstall 配 postinstall：按下「完成」時腳本已經不存在了")


class TestExitCodeContract:
    """離開碼是給 CodexAutoAI 的 fallback 判斷用的，文件與實作不能各說各話。"""

    def test_documented_codes_match_implementation(self):
        t = _text(PS1)
        header = t.split("#>", 1)[0]
        documented = {int(m) for m in re.findall(r"^\s*(\d)\s+\S", header, re.MULTILINE)}
        used = {int(m) for m in re.findall(r"Finish\s+(\d+)\s", t)}
        assert used <= documented, (
            f"這些離開碼有用但沒寫進說明：{sorted(used - documented)}")
        assert documented <= used, (
            f"這些離開碼寫了但沒人用：{sorted(documented - used)}")

    def test_json_mode_emits_exactly_one_line(self):
        """-Json 的輸出要能被程式解析，所以人類訊息一律不能進 stdout。

        `Say` 是唯一的輸出函式，而它在 -Json 時整個靜音——這條就是釘住那個性質。
        """
        t = _text(PS1)
        assert re.search(r"function Say\(.*\)\s*\{\s*if \(-not \$Json\)", t), (
            "Say 必須在 -Json 模式靜音，否則 JSON 那行會被雜訊淹沒")
        assert "ConvertTo-Json -Compress" in t, (
            "PS 5.1 的 ConvertTo-Json 會斷行，-Compress 才保證單行")


def test_build_refuses_a_script_without_bom():
    """build.ps1 自己要擋下沒有 BOM 的腳本——別讓壞檔案編進 .exe 流到使用者手上。"""
    t = _text(BUILD)
    assert "0xEF" in t and "0xBB" in t and "0xBF" in t, "build.ps1 應在編譯前檢查 BOM"


def test_build_looks_for_per_user_inno_install():
    """建置機沒有系統管理員是常態（本 App 自己就是 lowest 權限）——#71 的教訓。"""
    assert "LOCALAPPDATA" in _text(BUILD), "ISCC 候選路徑要含免管理員的 per-user 安裝位置"


class TestCrossPowerShellCompat:
    """安裝檔跑的是 **Windows 內建的 powershell.exe（5.1）**，不是 pwsh 7。

    實測差異：同一支腳本，pwsh 7 回 `loggedIn:true`、5.1 回 `false`。原因是
    `$ErrorActionPreference = "Stop"` 之下，node 往 stderr 印的 DEP0190 警告在 5.1
    會變成終止性錯誤，整段驗證被 catch 掉。若沒修，每個用 .exe 安裝的同事都會被
    告知「尚未登入」——即使他早就登入了。
    """

    def test_login_probe_tolerates_native_stderr(self):
        t = _text(PS1)
        assert "Get-CodexLoggedIn" in t, "登入偵測要收斂成一個函式，兩處呼叫才不會走樣"
        fn = t.split("function Get-CodexLoggedIn", 1)[1].split("\nfunction ", 1)[0]
        assert '$ErrorActionPreference = "Continue"' in fn, (
            "要暫時放行原生 stderr，否則 PS 5.1 會把 node 的警告當成終止性錯誤")
        assert "2>&1" in fn, "要把 stderr 一起收進來，不能丟掉（丟掉在 5.1 仍會觸發錯誤）"

    def test_login_probe_extracts_the_json_substring(self):
        """就算不終止，node 的警告也會混進輸出——不能整包丟給 ConvertFrom-Json。"""
        fn = _text(PS1).split("function Get-CodexLoggedIn", 1)[1].split("\nfunction ", 1)[0]
        assert 'IndexOf("{")' in fn and 'LastIndexOf("}")' in fn, (
            "要從輸出裡把 JSON 那段切出來再解析")

    def test_probe_distinguishes_unknown_from_false(self):
        """問不到狀態 ≠ 沒登入。混為一談會讓「驗證壞了」被講成「你沒登入」。"""
        fn = _text(PS1).split("function Get-CodexLoggedIn", 1)[1].split("\nfunction ", 1)[0]
        assert fn.count("return $null") >= 3, "問不到要回 $null，不是 $false"


def test_setup_does_not_leak_the_optional_step_exit_code():
    """步驟 7 是**選配**的：它回 6（只差登入）時，setup 整支不能跟著回 6。

    PowerShell 會沿用最後一個原生指令的離開碼，實測就是這樣把呼叫端誤導成
    「整個設定失敗」。
    """
    setup = Path(__file__).resolve().parents[1] / "setup.ps1"
    t = setup.read_bytes().decode("utf-8-sig")
    assert "Install-CodexPlugin.ps1" in t, "setup 應該會嘗試安裝 plugin"
    assert t.rstrip().endswith("exit 0"), "結尾要明確 exit 0，別沿用選配步驟的離開碼"


def test_setup_treats_the_plugin_as_optional():
    """plugin 失敗不能讓 setup 失敗——七階段 pipeline 走的是 codex_runner，不靠它。"""
    setup = Path(__file__).resolve().parents[1] / "setup.ps1"
    t = setup.read_bytes().decode("utf-8-sig")
    step7 = t.split("步驟 7", 1)[1]
    assert "try {" in step7 and "} catch {" in step7, "要包 try/catch，讓失敗只是略過"
    assert "不影響七階段 pipeline" in step7, "訊息要講清楚失敗的後果有限"


def test_logged_in_is_tri_state_not_boolean():
    """問不到登入狀態 ≠ 沒登入。

    `Get-CodexLoggedIn` 特地回 $null 表示「不知道」，但若 $result.loggedIn 的預設是
    $false，最終輸出仍會斷定「尚未登入」，把那個區分整個抹掉——使用者會被叫去跑一個
    他其實不需要跑的 codex login。
    """
    t = _text(PS1)
    assert "loggedIn = $null" in t, "預設要是 $null（不知道），不是 $false（確定沒登入）"
    assert "無法確認" in t, "問不到時的訊息要說「無法確認」，不能寫成斷定句"
