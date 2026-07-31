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


def _run(cmd: str, cwd: str, timeout: int) -> Ran:
    """跑一條 shell 指令並擷取輸出；**逾時不拋例外**，回傳 timed_out 結果。

    這是本工具唯一的子行程入口——裸的 ``subprocess.run`` 沒有 timeout，
    一個 hang 住的 `codex exec` 會無聲卡死整條 pipeline（沒有任何訊號）。
    """
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

    def _grounding(compiled, test_out):
        # 延遲匯入 review.py，套用 REVIEW-R2 的 grounding / skip 規則。
        from src.codexautoai_v2.review import GroundingSignals, require_grounding, should_skip_llm_review
        sig = GroundingSignals(compiled=compiled, test_output=test_out or "", lint_output="")
        require_grounding(sig)            # 確保審查錨定事實（不會 raise，compiled 為具體訊號）
        return should_skip_llm_review(sig)

    def review(fix, iteration):
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
            defects = parse_pytest_failures(proc.stdout, proc.stderr, proc.returncode)
            raw = proc.text
            tokens = 0
        else:
            defects = parse_issue_list(review_out)
            raw = _read(review_out)
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

    tmp = tempfile.mkdtemp(prefix="run_loop_")
    defects_file = str(Path(tmp) / "defects.txt")
    review_out = str(Path(tmp) / "review_out.txt")
    _write(defects_file, "")
    _write(review_out, "")
    boxes = {"cost": [0.0], "raw": [""], "fix_fail": [0], "fix_err": [""], "tiers": []}

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

    out = {"status": result.status, "iterations": result.iterations,
           "reason": reason, "final_defects": result.final_defects,
           "fixer_failures": fix_failures,
           "fixer_tiers": list(boxes["tiers"])}
    if boxes["fix_err"][0]:
        out["fixer_last_error"] = boxes["fix_err"][0][-500:]
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
