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
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent.parent


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
                    defects_file, review_out, boxes, compile_cmd=None, fix_retries=1):
    cost_box, raw_box = boxes["cost"], boxes["raw"]

    def _grounding(compiled, test_out):
        # 延遲匯入 review.py，套用 REVIEW-R2 的 grounding / skip 規則。
        from src.codexautoai_v2.review import GroundingSignals, require_grounding, should_skip_llm_review
        sig = GroundingSignals(compiled=compiled, test_output=test_out or "", lint_output="")
        require_grounding(sig)            # 確保審查錨定事實（不會 raise，compiled 為具體訊號）
        return should_skip_llm_review(sig)

    def review(fix, iteration):
        # REVIEW-R2-S2：若有 compile 步驟且編譯失敗，跳過昂貴的 reviewer/測試，直接 fix。
        if compile_cmd:
            cp = subprocess.run(_subst(compile_cmd, iteration, defects_file, review_out),
                               shell=True, cwd=workdir, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if _grounding(cp.returncode == 0, (cp.stdout or "") + (cp.stderr or "")):
                raw = (cp.stdout or "") + "\n" + (cp.stderr or "")
                _write(defects_file, raw); raw_box[0] = raw
                orch.events.emit("loop_tick", phase=phase_label, iteration=iteration,
                                cumulative_cost_usd=round(cost_box[0] / 1000.0, 6),
                                status="in_progress")
                return {"defects": ["compile:failed"], "tokens": 0}
        cmd = _subst(review_cmd, iteration, defects_file, review_out)
        proc = subprocess.run(cmd, shell=True, cwd=workdir,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if mode == "test":
            defects = parse_pytest_failures(proc.stdout, proc.stderr, proc.returncode)
            raw = (proc.stdout or "") + "\n" + (proc.stderr or "")
            tokens = 0
        else:
            defects = parse_issue_list(review_out)
            raw = _read(review_out)
            tokens = estimate_tokens(cmd, proc.stdout)
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
        cmd = _subst(fix_cmd, iteration, defects_file, review_out)
        # Codex CLI 薄 retry：非零 exit 先便宜地重試，再交給下一輪 review 判定。
        for _ in range(max(1, fix_retries)):
            p = subprocess.run(cmd, shell=True, cwd=workdir, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if p.returncode == 0:
                break
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
    boxes = {"cost": [0.0], "raw": [""]}

    produce_fix, review = _make_callables(
        orch, args.mode, phase_label, workdir,
        args.review_cmd, args.fix_cmd, defects_file, review_out, boxes,
        compile_cmd=args.compile_cmd, fix_retries=args.fix_retries)

    result = orch.run_fix_loop(
        produce_fix=produce_fix, review=review,
        max_iterations=args.max_iters, patience=args.patience,
        max_tokens=args.max_tokens, phase=phase_label)

    out = {"status": result.status, "iterations": result.iterations,
           "reason": result.reason, "final_defects": result.final_defects}
    if result.status == "escalated":
        orch.events.emit("error", phase=phase_label,
                        reason=result.reason or "escalated", status="escalated")
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
                    help="fix-cmd 非零 exit 時的便宜 CLI 重試次數（預設 1）")
    ap.add_argument("--reviewer-model")
    ap.add_argument("--fixer-model")
    ap.add_argument("--available")
    args = ap.parse_args(argv)

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
