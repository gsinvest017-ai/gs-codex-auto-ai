#!/usr/bin/env python3
"""
run_loop.py — Stage 2：把有界 fix 迴圈交給 Python 的 Orchestrator 確定性擁有。

舊版叫 LLM「循環直到通過」是 honor-system，三道終止守衛形同虛設。本工具改由
`Orchestrator.run_fix_loop` 真正擁有 while + max_iterations / no-progress / budget
三守衛 + escalation，LLM/Codex 的工作限縮在注入的 produce_fix / review callable，
輸出由 Python gate（這正是 orchestrator.py docstring 描述的 Model A）。

兩種模式：
  --mode test   ：Phase 6 test-fix。review=跑測試解析失敗 node id；fix=codex 改 src/。
  --mode review ：Phase 4 review-fix。review=codex(模型 A) 比對 spec↔架構輸出問題清單；
                  fix=codex(模型 B) 改 architecture.md。reviewer≠fixer 以兩個不同模型滿足
                  REVIEW-R1/C5（單行程，避免跨行程丟失 in-memory 守衛狀態）。

用法：
  python tools/run_loop.py --mode test --phase 6 [--run-id ID] \
     [--max-iters 3] [--patience 2] [--max-tokens N] [--workdir DIR] \
     --review-cmd "<樣板>" --fix-cmd "<樣板>"
  python tools/run_loop.py --mode review --phase 4 \
     --reviewer-model A --fixer-model B --available A,B \
     --review-cmd "<樣板 寫 {review_out}>" --fix-cmd "<樣板 讀 {review_out}>"

樣板佔位符：{iteration} {defects_file}（上一輪 review 的原始輸出）{review_out}（review 解析輸出）。
輸出：stdout 印一行 RunResult JSON；exit 0=resolved/error(fail-safe)、3=escalated。
憲章：永不 commit/push（C6）；Codex 輸出只當資料、regex 抽 id，永不 eval（C10）；
      時間戳由系統時鐘（C3）。

三個可靠性保證（皆為預設行為，無需開關）：
  1. **一律有逾時**（`--review-timeout` / `--fix-timeout`）。所有子行程都走 `_run`，
     hang 住的 `codex exec` 不再無聲卡死整條 pipeline；review 逾時合成
     `review:timeout` 缺陷，**絕不當成通過**。
  2. **修復器失敗會進入下一輪 prompt**。Codex 非零 exit / 逾時時把 stderr 尾段併進
     `{defects_file}`，並在 escalation reason 標記 `fixer_failed`——否則缺陷集完全
     相同、no-progress 會把「Codex 沒跑起來」誤報成「測試修不動」。
  3. **地端先試修**（`--local-fix-cmd` + `--max-local-attempts`，借鏡 gs-agent-router
     的 escalate 分層）：前 N 輪走便宜的地端修復器，之後才升級雲端 Codex。外層迴圈的
     review 就是 verifier，不多花一次驗證成本。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent.parent

# 逾時預設值（秒）。Codex 修復比跑測試久得多，故給不同預設。
DEFAULT_REVIEW_TIMEOUT = 900
DEFAULT_FIX_TIMEOUT = 1800


class Ran:
    """一次子行程執行的結果（逾時不拋例外，改回傳 ``timed_out=True``）。

    語意移植自 gs-common 的 ``gs_common.proc.run``；此處**重寫而非 import**，
    因為 gs-common 是 private repo，而本框架是公開發行、且會被丟進使用者專案裡
    直接跑，不能引入私有或第三方依賴（同 `desktop/launcher.py` 的純標準庫不變式）。

    **刻意不用 `@dataclass`**：本檔會被 `importlib.util.module_from_spec` 之類的
    載入器直接 exec（測試與被丟進他人專案時都是），那條路徑不會把模組註冊進
    `sys.modules`，而 `dataclasses` 內部要靠 `sys.modules[cls.__module__]` 解析
    型別註解，會炸 `AttributeError: 'NoneType' object has no attribute '__dict__'`。
    手寫 `__init__` 對載入方式零假設。
    """

    def __init__(self, returncode: int, stdout: str, stderr: str,
                 timed_out: bool = False) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def text(self) -> str:
        return (self.stdout or "") + "\n" + (self.stderr or "")

    def ran_pytest(self) -> bool:
        """輸出裡有沒有 pytest 真的跑過的痕跡。

        **不能只靠錯誤訊息比對**：cmd.exe 的訊息是在地化的（實測這台中文 Windows
        給的是「系統找不到指定的路徑」，英文樣式一條都對不上），而且 rc 只是 1，
        跟「測試失敗」無從分辨。改看 pytest 自己的招牌字串——它跑過就一定會留下，
        跟語系無關。

        刻意**不收**裸的 ``error``：那兩個字幾乎任何錯誤訊息裡都有，會把真正的
        啟動失敗誤判成「跑過了」。pytest 的 error 摘要一定伴隨 ``=====`` 分隔線，
        所以不會漏。
        """
        low = self.text.lower()
        return any(s in low for s in (
            "passed", "failed", "collected", "no tests ran",
            "=====", "test session starts",
        ))


def _kill_tree(proc: subprocess.Popen) -> None:
    """殺掉整棵行程樹（**不只是直屬子行程**）。

    `shell=True` 下 Python 的子行程是 shell，`codex` / `node` 是孫行程。
    只殺 shell 會讓孫行程變孤兒**並繼續持有 stdout pipe**，於是後續的
    `communicate()` 仍會阻塞到孫行程自己結束——逾時等於沒生效。
    Windows 用 `taskkill /T`（整棵樹）、POSIX 用 process group signal。
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except Exception:
        pass


def win_shell_cmd(cmd: str) -> str:
    """Windows 上把**開頭那個執行檔路徑**的正斜線換成反斜線。

    `shell=True` 在 Windows 上是 `cmd.exe /c <cmd>`，而 cmd.exe 不吃正斜線開頭的
    路徑——`.venv/Scripts/python -m pytest` 會直接回「系統找不到指定的路徑」。
    CLAUDE.md 裡的範例、skill 文件與 POSIX 習慣都寫正斜線，所以這個地雷是必然會踩的。

    只動第一個 token（真正交給 cmd.exe 當程式名的那個），後面的參數原封不動——
    參數裡的正斜線常常是**旗標**（`/c`）或**要傳給程式的路徑**，改了會壞事。
    非 Windows、看起來不像路徑（沒有 `/`）、或是 URL 的一律不碰。
    """
    if os.name != "nt" or not cmd[:1].strip():
        return cmd
    # **要切在第一個「引號外」的空白，不是第一個空白。** Windows 上帶空白的路徑
    # （`"C:/Users/Jane Doe/.venv/Scripts/python" -m pytest`）極常見，naive 的
    # partition(" ") 只會轉換空白之前那半段，留下一個半吊子的混合路徑。
    #
    # 誠實記錄：實測 cmd.exe **容忍**引號內混用 `/` 與 `\`，所以那個半吊子結果照樣
    # 跑得起來，我構造不出它真的失敗的案例（見對應測試）。這裡改的理由是「整個
    # 執行檔 token 一起轉換」說得清自己在做什麼，不是修掉了一個會爆的 bug。
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end == -1:                       # 引號沒收尾，別亂動
            return cmd
        head, rest = cmd[:end + 1], cmd[end + 1:]
        sep = ""
    else:
        head, sep, rest = cmd.partition(" ")
    if "://" in head or "/" not in head:
        return cmd
    return head.replace("/", "\\") + sep + rest


def _slug(text: str) -> str:
    """把 run_id / phase 變成安全的資料夾名（它們最終會進檔案系統路徑）。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)[:80] or "run"


def final_reason(reason: str | None, tool_fail: str, status: str) -> str | None:
    """收尾時的 reason：工具層失敗要講出來，但**收斂成功的 run 不算**。

    講出來的理由：使用者看到 no_progress 會以為「東西修不好」，實際上是指令打不開
    （路徑、venv、額度…），方向完全錯。

    加上 status 條件的理由：`tool_fail` 是「最後一輪指令有沒有跑起來」。就算它逐輪
    被清乾淨了（見 `review()`），這裡仍留一道——一個已經 resolved 的 run 宣稱自己
    因為指令打不開而失敗，跟 M1 想解決的混淆是同一件事的反面。兩道防線是刻意的：
    上游少清一次，這裡還攔得住。
    """
    if tool_fail and status != "resolved":
        return f"tool_failed（指令無法執行，不是缺陷修不動）：{tool_fail}"
    return reason


def _run(cmd: str, cwd: str, timeout: int) -> Ran:
    """跑一條 shell 指令並擷取輸出；**逾時不拋例外**，回傳 timed_out 結果。

    這是本工具唯一的子行程入口——裸的 ``subprocess.run`` 沒有 timeout，
    一個 hang 住的 `codex exec` 會無聲卡死整條 pipeline（沒有任何訊號）。
    """
    cmd = win_shell_cmd(cmd)
    kwargs: dict = dict(shell=True, cwd=cwd, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True,
                        encoding="utf-8", errors="replace")
    if os.name != "nt":
        # 自己開一個 process group，讓逾時能整組殺掉。
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
        return Ran(proc.returncode, out or "", err or "")
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            # 收殘餘輸出，但給有限寬限期，避免又卡在同一個 pipe 上。
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return Ran(-1, out or "", err or "", timed_out=True)


def _as_text(stream: object) -> str:
    """TimeoutExpired 的 stdout/stderr 可能是 bytes / str / None，統一成 str。"""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", "replace")
    return str(stream)


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else _TOOL_ROOT


def _paths(root: Path) -> dict:
    log = root / "log"
    return {
        "events": str(log / "events.jsonl"),
        "audit": str(log / "audit.jsonl"),
        "state": str(log / "state.json"),
        "run_ptr": log / "current_run.txt",
    }


def _resolve_run_id(paths: dict, explicit: str | None) -> str:
    if explicit:
        return explicit
    ptr: Path = paths["run_ptr"]
    try:
        if ptr.exists():
            v = ptr.read_text(encoding="utf-8").strip()
            if v:
                return v
    except Exception:
        pass
    from datetime import datetime
    return "run-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def _build_orch(paths: dict, run_id: str):
    if str(_TOOL_ROOT) not in sys.path:
        sys.path.insert(0, str(_TOOL_ROOT))
    from src.codexautoai_v2.orchestrator import Orchestrator  # noqa: E402
    return Orchestrator(
        event_path=paths["events"], audit_path=paths["audit"],
        state_path=paths["state"], run_id=run_id,
    )


def _phase_label(n: str) -> str:
    s = str(n).lower()
    return s if s.startswith("phase") else f"phase{s}"


# ---------------------------------------------------------------------------
# 解析器（純函式，可單獨測試）
# ---------------------------------------------------------------------------
_FAIL_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_ISSUE_RE = re.compile(r"^\s*(MISSING|EXTRA|MISMATCH):(\S+)", re.MULTILINE)


def parse_pytest_failures(stdout: str, stderr: str, returncode: int) -> list[str]:
    """把 pytest 輸出解析成穩定的 node id 集合（餵 no-progress hash）。"""
    text = (stdout or "") + "\n" + (stderr or "")
    ids = sorted(set(_FAIL_RE.findall(text)))
    if ids:
        return ids
    if returncode == 0:
        return []                      # 真的全過
    if returncode == 5:
        return ["pytest:no-tests"]     # 沒收集到測試 = 缺陷，不是通過
    # 非零但解析不到具體失敗（crash/collection error）→ 合成缺陷，絕不假裝通過
    return ["pytest:unknown-failure"]


def parse_issue_list(out_file: str) -> list[str]:
    """從 review 寫出的 {review_out} 抽封閉詞彙 TYPE:ID（hash 穩定）。"""
    try:
        text = Path(out_file).read_text(encoding="utf-8")
    except Exception:
        return []
    return sorted({f"{m[0]}:{m[1]}" for m in _ISSUE_RE.findall(text)})


def estimate_tokens(*parts: str) -> int:
    return sum(len(p) for p in parts if p) // 4


def _subst(template: str, iteration: int, defects_file: str, review_out: str) -> str:
    return (template
            .replace("{iteration}", str(iteration))
            .replace("{defects_file}", defects_file)
            .replace("{review_out}", review_out))


# ---------------------------------------------------------------------------
# callable 工廠
# ---------------------------------------------------------------------------
def _make_callables(orch, mode, phase_label, workdir, review_cmd, fix_cmd,
                    defects_file, review_out, boxes, compile_cmd=None, fix_retries=1,
                    review_timeout=DEFAULT_REVIEW_TIMEOUT,
                    fix_timeout=DEFAULT_FIX_TIMEOUT,
                    local_fix_cmd=None, max_local_attempts=0):
    cost_box, raw_box = boxes["cost"], boxes["raw"]
    fixfail_box, fixerr_box, tier_box = boxes["fix_fail"], boxes["fix_err"], boxes["tiers"]
    toolfail_box = boxes["tool_fail"]

    def _grounding(compiled, test_out):
        # 延遲匯入 review.py，套用 REVIEW-R2 的 grounding / skip 規則。
        from src.codexautoai_v2.review import GroundingSignals, require_grounding, should_skip_llm_review
        sig = GroundingSignals(compiled=compiled, test_output=test_out or "", lint_output="")
        require_grounding(sig)            # 確保審查錨定事實（不會 raise，compiled 為具體訊號）
        return should_skip_llm_review(sig)

    def review(fix, iteration):
        # 每輪先清掉上一輪的工具層失敗。這個 box 是「**這一輪**指令有沒有跑起來」，
        # 不是「整輪跑下來曾經失敗過」——不清的話，第一輪的短暫失敗（檔案鎖、防毒
        # 干擾）會在後面幾輪恢復、真的收斂成功之後，仍然把最終 reason 蓋成
        # tool_failed，等於用另一種方式犯下 M1 要修的那個錯。
        toolfail_box[0] = ""
        # REVIEW-R2-S2：若有 compile 步驟且編譯失敗，跳過昂貴的 reviewer/測試，直接 fix。
        if compile_cmd:
            cp = _run(_subst(compile_cmd, iteration, defects_file, review_out),
                      workdir, review_timeout)
            if _grounding(cp.ok, cp.text):
                raw = cp.text
                if cp.timed_out:
                    raw += f"\n[compile 逾時 {review_timeout}s，視為編譯失敗]"
                _write(defects_file, raw); raw_box[0] = raw
                orch.events.emit("loop_tick", phase=phase_label, iteration=iteration,
                                cumulative_cost_usd=round(cost_box[0] / 1000.0, 6),
                                status="in_progress")
                return {"defects": ["compile:failed"], "tokens": 0}
        cmd = _subst(review_cmd, iteration, defects_file, review_out)
        proc = _run(cmd, workdir, review_timeout)
        if mode == "test":
            if not proc.ok and not proc.ran_pytest():
                # 工具層失敗 ≠ 語意結論。硬報成測試失敗的話，缺陷每輪都一樣，
                # 迴圈會以 no_progress 收場，把「我叫不動 pytest」講成「測試修不動」。
                #
                # **只用 `ran_pytest()` 判**。曾經另有一個 `launch_failed` 比對
                # shell 的錯誤訊息，但那條路有兩個問題：訊息會在地化（中文 Windows
                # 一條都對不上），而且 "the system cannot find the file specified"
                # 正是 Windows FileNotFoundError 的標準文字，會出現在**真的**測試
                # 失敗的 traceback 裡，反而把真缺陷蓋成工具層失敗。
                # 「pytest 有沒有留下痕跡」跟語系無關，也不會被 traceback 內容騙。
                toolfail_box[0] = (f"測試指令無法執行：{cmd}\n"
                                   f"{proc.text.strip()[:400]}")
                defects = ["tool:cannot-run-tests"]
            else:
                defects = parse_pytest_failures(proc.stdout, proc.stderr, proc.returncode)
            raw = proc.text
            tokens = 0
        else:
            defects = parse_issue_list(review_out)
            raw = _read(review_out)
            # 「reviewer 說沒缺陷」與「reviewer 根本沒產出」都是空的 {review_out}，
            # 但前者該收斂、後者是工具層失敗。不分開就會假通過（實測 Phase 4
            # 連兩次假通過）。
            if not defects and not raw.strip() and not proc.ok:
                toolfail_box[0] = (f"審查指令沒有產出 {review_out}：{cmd}\n"
                                   f"{proc.text.strip()[:400]}")
                defects = ["tool:review-no-output"]
            tokens = estimate_tokens(cmd, proc.stdout)
        # review 逾時絕不能被當成「通過」——合成一個穩定缺陷讓迴圈繼續收斂。
        if proc.timed_out:
            defects = sorted(set(defects) | {"review:timeout"})
            raw += f"\n[review 指令逾時 {review_timeout}s]"
        # 上一輪 fixer 的失敗訊息要帶進 prompt 素材，否則 Codex 沒跑成功時
        # 下一輪看到的缺陷完全相同、no-progress 會誤判成「測試修不動」。
        if fixerr_box[0]:
            raw += f"\n\n[上一輪修復器失敗，原始錯誤]\n{fixerr_box[0]}"
        # 把這一輪的原始輸出留給下一輪 produce_fix 當 prompt 素材（資料，不插指令）
        _write(defects_file, raw)
        raw_box[0] = raw
        cost_box[0] += tokens
        orch.events.emit("loop_tick", phase=phase_label, iteration=iteration,
                        cumulative_cost_usd=round(cost_box[0] / 1000.0, 6),
                        status="in_progress")
        return {"defects": defects, "tokens": tokens}

    def produce_fix(iteration):
        # 迴圈是 fix→review；第 0 輪沒有前一輪 review，跳過讓 review 先建立缺陷集。
        if iteration == 0 or not raw_box[0].strip():
            return {"diff": "", "tokens": 0}
        # 地端先試修（借鏡 gs-agent-router 的 escalate 分層）：前 max_local_attempts
        # 輪走便宜的地端修復器，之後才升級雲端 Codex。外層迴圈的 review 就是
        # verifier，不需要巢狀驗證、不會多花一次測試成本。
        use_local = bool(local_fix_cmd) and iteration <= max_local_attempts
        tier = "local" if use_local else "cloud"
        template = local_fix_cmd if use_local else fix_cmd
        cmd = _subst(template, iteration, defects_file, review_out)

        # 薄 retry：非零 exit / 逾時先便宜地重試，再交給下一輪 review 判定。
        last = None
        for _ in range(max(1, fix_retries)):
            last = _run(cmd, workdir, fix_timeout)
            if last.ok:
                break
        tier_box.append(tier)
        if last is not None and not last.ok:
            fixfail_box[0] += 1
            why = f"逾時 {fix_timeout}s" if last.timed_out else f"exit {last.returncode}"
            # 只留尾段，避免把整份輸出灌進下一輪 prompt。
            tail = (last.stderr or last.stdout or "").strip()[-2000:]
            fixerr_box[0] = f"[{tier} 修復器{why}]\n{tail}"
            orch.events.emit("tool_call", phase=phase_label, iteration=iteration,
                            tool=f"fixer:{tier}", status="failure", reason=why)
        else:
            fixerr_box[0] = ""
        diff = _git_diff_stat(workdir)            # 唯讀，永不 commit（C6）
        tokens = estimate_tokens(cmd, raw_box[0])
        cost_box[0] += tokens
        return {"diff": diff, "tokens": tokens}

    return produce_fix, review


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


def _write(path: str, text: str) -> None:
    try:
        Path(path).write_text(text, encoding="utf-8")
    except Exception:
        pass


def _git_diff_stat(workdir: str) -> str:
    try:
        p = subprocess.run(["git", "-C", workdir, "diff", "--stat"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
        return p.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(args) -> dict:
    root = _project_dir()
    paths = _paths(root)
    run_id = _resolve_run_id(paths, args.run_id)
    orch = _build_orch(paths, run_id)
    phase_label = _phase_label(args.phase)
    workdir = args.workdir or str(root)

    # REVIEW-R1：mode=review 記錄 reviewer≠fixer 的獨立性
    if args.mode == "review" and args.available:
        avail = [m.strip() for m in args.available.split(",") if m.strip()]
        try:
            orch.pick_reviewer(args.fixer_model or "", avail)
        except Exception:
            pass

    # **放進專案內**：`tempfile.mkdtemp()` 不受 `--workdir` 控制，恆落在系統 temp，
    # 而 Codex 的 sandbox 只寫得到工作目錄——reviewer 寫不出 {review_out}，
    # 迴圈就會把「沒產出」當成「沒缺陷」。放在 log/ 底下同時也留了現場可查。
    # 帶上 run_id / phase：同一個 workdir 上有兩個 run_loop 同時跑時（重疊的 run、
    # 未來的並行使用）不會互相蓋掉對方的 defects.txt / review_out.txt。
    # 換掉 mkdtemp 是為了 sandbox 可寫性，但不該連隔離性一起丟掉。
    tmp = str(Path(workdir) / "log" / "run_loop" / _slug(f"{run_id}-{phase_label}"))
    try:
        Path(tmp).mkdir(parents=True, exist_ok=True)
    except OSError:
        tmp = tempfile.mkdtemp(prefix="run_loop_")   # 專案不可寫時的退路
    defects_file = str(Path(tmp) / "defects.txt")
    review_out = str(Path(tmp) / "review_out.txt")
    _write(defects_file, "")
    _write(review_out, "")
    boxes = {"cost": [0.0], "raw": [""], "fix_fail": [0], "fix_err": [""],
             "tiers": [], "tool_fail": [""]}

    # getattr 取值：`run()` 也被測試與其他呼叫端用手搭的 Namespace 驅動，
    # 不強制它們補齊新旗標（缺省即沿用預設行為）。
    produce_fix, review = _make_callables(
        orch, args.mode, phase_label, workdir,
        args.review_cmd, args.fix_cmd, defects_file, review_out, boxes,
        compile_cmd=getattr(args, "compile_cmd", None),
        fix_retries=getattr(args, "fix_retries", 1),
        review_timeout=getattr(args, "review_timeout", DEFAULT_REVIEW_TIMEOUT),
        fix_timeout=getattr(args, "fix_timeout", DEFAULT_FIX_TIMEOUT),
        local_fix_cmd=getattr(args, "local_fix_cmd", None),
        max_local_attempts=getattr(args, "max_local_attempts", 0))

    result = orch.run_fix_loop(
        produce_fix=produce_fix, review=review,
        max_iterations=args.max_iters, patience=args.patience,
        max_tokens=args.max_tokens, phase=phase_label)

    fix_failures = boxes["fix_fail"][0]
    reason = result.reason
    # 守衛觸發時，區分「測試真的修不動」與「修復器根本沒跑成功」——後者是環境問題
    # （額度耗盡 / 未登入 / sandbox 拒寫），報成 no_progress 會把人導向錯的地方。
    if result.status == "escalated" and fix_failures and len(boxes["tiers"]) == fix_failures:
        reason = f"fixer_failed（修復器 {fix_failures} 次全部失敗，非缺陷修不動）：{reason}"
    reason = final_reason(reason, boxes["tool_fail"][0], result.status)

    out = {"status": result.status, "iterations": result.iterations,
           "reason": reason, "final_defects": result.final_defects,
           "fixer_failures": fix_failures,
           "fixer_tiers": list(boxes["tiers"])}
    if boxes["fix_err"][0]:
        out["fixer_last_error"] = boxes["fix_err"][0][-500:]
    if boxes["tool_fail"][0]:
        out["tool_failure"] = boxes["tool_fail"][0][-500:]
    if result.status == "escalated":
        orch.events.emit("error", phase=phase_label,
                        reason=reason or "escalated", status="escalated")
    return out


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="CodexAutoAI 有界 fix 迴圈（Python 擁有迴圈與守衛）")
    ap.add_argument("--mode", choices=["test", "review"], required=True)
    ap.add_argument("--phase", required=True)
    ap.add_argument("--run-id")
    ap.add_argument("--max-iters", type=int, default=3)
    ap.add_argument("--patience", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--workdir")
    ap.add_argument("--review-cmd", required=True)
    ap.add_argument("--fix-cmd", required=True)
    ap.add_argument("--compile-cmd", default=None,
                    help="可選：先跑編譯/語法檢查；失敗則跳過 reviewer 直接 fix（REVIEW-R2-S2 省成本）")
    ap.add_argument("--fix-retries", type=int, default=1,
                    help="fix-cmd 非零 exit / 逾時的便宜 CLI 重試次數（預設 1）")
    ap.add_argument("--review-timeout", type=int, default=DEFAULT_REVIEW_TIMEOUT,
                    help=f"review/compile 指令逾時秒數（預設 {DEFAULT_REVIEW_TIMEOUT}）；"
                         "逾時視為缺陷而非通過")
    ap.add_argument("--fix-timeout", type=int, default=DEFAULT_FIX_TIMEOUT,
                    help=f"fix 指令逾時秒數（預設 {DEFAULT_FIX_TIMEOUT}）；"
                         "hang 住的 codex exec 不再無聲卡死 pipeline")
    ap.add_argument("--local-fix-cmd", default=None,
                    help="可選：地端便宜修復器樣板（如接 Ollama/klaude）。前 "
                         "--max-local-attempts 輪走這個，之後才升級雲端 --fix-cmd")
    ap.add_argument("--max-local-attempts", type=int, default=0,
                    help="地端先試修的輪數（預設 0=關閉）。**必須小於 --max-iters**，"
                         "否則雲端修復器永遠不會被呼叫")
    ap.add_argument("--reviewer-model")
    ap.add_argument("--fixer-model")
    ap.add_argument("--available")
    args = ap.parse_args(argv)

    # 地端輪數吃掉全部迭代 → 雲端永遠輪不到，等於默默關掉升級路徑。明確警告。
    if args.local_fix_cmd and args.max_local_attempts >= args.max_iters:
        print(f"[run_loop] 警告：--max-local-attempts={args.max_local_attempts} >= "
              f"--max-iters={args.max_iters}，雲端 --fix-cmd 永遠不會被呼叫。",
              file=sys.stderr)

    try:
        out = run(args)
    except Exception as exc:  # noqa: BLE001 — fail-safe：工具層錯誤不視為通過
        print(json.dumps({"status": "error", "reason": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False))
        return 0
    print(json.dumps(out, ensure_ascii=False))
    return 3 if out["status"] == "escalated" else 0


if __name__ == "__main__":
    raise SystemExit(main())
