#!/usr/bin/env python3
"""
run_build.py — Stage 3：Phase 5 確定性建置 gate + Phase 4.5 屬性測試生成。

三個子命令，全部重用 v2 既有模組，不重寫：

  plan       由 docs/fn-manifest.json 跑 `Orchestrator.plan_build`：
             拓樸排序 + **循環依賴拒絕**（ORCH-R6）+ 檔案所有權批次切分（BUILD-R2）。
             純計算、不動 git。印出批次計畫；偵測到環 → exit 3 升級。

  gen-tests  由 spec（含 EARS GIVEN/WHEN/THEN scenario）用 property_verifier.stub_for
             產生**失敗的 pytest stub**（REVIEW-R3）。這些 stub 會被 Phase 6 的
             run_loop 驅動 Codex 補實作，把 EARS 條件變成可執行檢查。

  gate       Phase 5 **每批建置後**的便宜語法 gate（重用 `syntax_guard`，零 LLM）。
             在這之前 Phase 5 沒有任何修復迴圈——Codex 寫壞的東西要等整個 Phase 5
             跑完、進 Phase 6 才第一次拿到訊號。這個 gate 讓語法/JSON 壞掉在當批
             就被抓到。status=syntax_error → exit 3，呼叫端就地跑 run_loop 收斂。

  build      （opt-in，預設不跑）worktree 隔離並行建置（BUILD-R1/R3）：每個 owner_file
             一個 git worktree，build_fn 在其中跑 --build-cmd 並 commit，最後 3-way
             merge 回目標分支。**安全護欄**：--repo-root 必須是「目標專案 git」且≠框架 repo，
             否則拒絕（避免把生成碼 merge 進框架 main，C6）。永不 push。

manifest 格式（list）：{"id","file","deps":[id...],"signature"?,"ears"?}
（`file`=該 FN 寫入的檔；`deps`=前置 FN id；plan_build 用 id/deps 偵測環、用 file 切批次。）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent.parent


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else _TOOL_ROOT


def _paths(root: Path) -> dict:
    log = root / "log"
    return {"events": str(log / "events.jsonl"), "audit": str(log / "audit.jsonl"),
            "state": str(log / "state.json"), "run_ptr": log / "current_run.txt"}


def _resolve_run_id(paths: dict, explicit: str | None) -> str:
    if explicit:
        return explicit
    ptr: Path = paths["run_ptr"]
    try:
        if ptr.exists() and ptr.read_text(encoding="utf-8").strip():
            return ptr.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    from datetime import datetime
    return "run-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def _imports():
    if str(_TOOL_ROOT) not in sys.path:
        sys.path.insert(0, str(_TOOL_ROOT))
    from src.codexautoai_v2.orchestrator import Orchestrator
    from src.codexautoai_v2.depgraph import CycleError
    from src.codexautoai_v2 import property_verifier as pv
    return Orchestrator, CycleError, pv


def _syntax_guard():
    if str(_TOOL_ROOT) not in sys.path:
        sys.path.insert(0, str(_TOOL_ROOT))
    from src.codexautoai_v2 import syntax_guard
    return syntax_guard


def _build_orch(paths, run_id):
    Orchestrator, _, _ = _imports()
    return Orchestrator(event_path=paths["events"], audit_path=paths["audit"],
                        state_path=paths["state"], run_id=run_id)


# ---------------------------------------------------------------------------
def cmd_plan(args) -> dict:
    _, CycleError, _ = _imports()
    fns = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    root = _project_dir(); paths = _paths(root)
    orch = _build_orch(paths, _resolve_run_id(paths, args.run_id))
    try:
        batches = orch.plan_build(fns)   # topological_batches(環→CycleError) + partition
    except CycleError as exc:
        orch.events.emit("error", phase="phase5", reason="dependency_cycle",
                        status="escalated")
        return {"status": "escalated", "reason": "dependency_cycle",
                "cycle": getattr(exc, "cycle", None)}
    return {"status": "planned", "batch_count": len(batches), "batches": batches}


def cmd_gen_tests(args) -> dict:
    _, _, pv = _imports()
    md = Path(args.spec).read_text(encoding="utf-8")
    props = pv.parse_scenarios(md)
    if not props:
        return {"status": "no_scenarios", "count": 0}
    body = ["# 由 run_build.py gen-tests 從 EARS scenario 生成（REVIEW-R3）。",
            "# 這些 stub 預設失敗，待 Phase 6 的 run_loop 驅動實作補齊。", ""]
    # 走 stubs_for 而不是逐個 stub_for——它保證整批名字唯一，中文場景名不會撞名互蓋
    body += pv.stubs_for(props)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(body), encoding="utf-8")
    return {"status": "generated", "count": len(props), "out": str(out)}


def cmd_gate(args) -> dict:
    """Phase 5 每批建置後的**便宜語法 gate**（重用 `syntax_guard`，零 LLM、零測試成本）。

    在這之前，Phase 5 完全沒有修復迴圈：Codex 寫壞的東西要等整個 Phase 5 跑完、
    進 Phase 6 跑測試才第一次拿到訊號。這個 gate 讓「語法/JSON 壞掉」這類最常見、
    最好修的錯誤在**當批**就被抓到，不必累積到跨 phase 才反饋。

    收檔案清單，或從 manifest 推出該批的 owner_file。回傳 JSON，
    `status=syntax_error` 時 exit 3，呼叫端據此就地跑 run_loop 收斂。
    """
    sg = _syntax_guard()

    files: list[str] = list(args.files or [])
    if args.manifest:
        fns = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        manifest_files = [fn["file"] for fn in fns if fn.get("file")]
        if args.batch is not None:
            root = _project_dir(); paths = _paths(root)
            orch = _build_orch(paths, _resolve_run_id(paths, args.run_id))
            batches = orch.plan_build(fns)
            # **1-based**：plan 的輸出與 skill 文件都講「第 N 批」，而這裡以前是
            # 0-based——`--batch 1` 檢查的其實是第 2 批（還沒建的檔），等於整個
            # Phase 5 都在檢查不存在的東西，語法保護形同虛設。兩邊語義對齊，
            # 並明確拒絕 0 而不是默默當成第 1 批。
            if args.batch < 1 or args.batch > len(batches):
                return {"status": "error",
                        "reason": (f"batch {args.batch} 超出範圍："
                                   f"批次從 1 開始，共 {len(batches)} 批")}
            manifest_files = [a["owner_file"] for a in batches[args.batch - 1]
                              if a.get("owner_file")]
        files.extend(manifest_files)

    # 去重但保留順序，讓輸出可預期
    seen: set[str] = set()
    ordered = [f for f in files if not (f in seen or seen.add(f))]
    if not ordered:
        return {"status": "no_files", "checked": 0, "failures": []}

    failures = []
    checked = 0
    for rel in ordered:
        p = Path(rel)
        if not p.is_absolute():
            p = _project_dir() / rel
        if not p.exists():
            # 檔案沒生出來本身就是缺陷（Codex 沒寫、或寫到別的位置）
            failures.append({"file": rel, "line": None, "message": "檔案不存在（Codex 未產出？）"})
            continue
        try:
            source = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            failures.append({"file": rel, "line": None, "message": f"讀取失敗：{exc}"})
            continue
        checked += 1
        result = sg.check(rel, source)
        if not result.ok:
            failures.append({"file": rel, "line": getattr(result, "line", None),
                             "message": getattr(result, "message", "syntax error")})

    if failures:
        root = _project_dir(); paths = _paths(root)
        orch = _build_orch(paths, _resolve_run_id(paths, args.run_id))
        # 刻意**不**發 event_type="error"：那會被 events_model 判成整條 run 升級失敗，
        # 但語法 gate 失敗是可就地修復的中間狀態（呼叫端接著跑 run_loop 收斂）。
        orch.events.emit("tool_call", phase="phase5", tool="syntax_gate",
                        status="failure", reason="syntax_gate_failed",
                        failed_files=[f["file"] for f in failures])
        return {"status": "syntax_error", "checked": checked, "failures": failures}
    return {"status": "ok", "checked": checked, "failures": []}


def cmd_build(args) -> dict:
    Orchestrator, CycleError, _ = _imports()
    repo_root = Path(args.repo_root).resolve()
    # 安全護欄（C6）：不得在框架 repo 內做 worktree-merge 建置
    if repo_root == _TOOL_ROOT.resolve():
        return {"status": "refused",
                "reason": "repo_root 不可為框架 repo；請指定獨立的目標專案目錄"}
    fns = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    root = _project_dir(); paths = _paths(root)
    orch = _build_orch(paths, _resolve_run_id(paths, args.run_id))
    try:
        batches = orch.plan_build(fns)
    except CycleError:
        orch.events.emit("error", phase="phase5", reason="dependency_cycle", status="escalated")
        return {"status": "escalated", "reason": "dependency_cycle"}

    def build_fn(wt, assignment):
        owner = assignment.get("owner_file", "")
        cmd = (args.build_cmd.replace("{worktree}", wt)
               .replace("{owner_file}", owner)
               .replace("{fns}", ",".join(assignment.get("fns", []))))
        subprocess.run(cmd, shell=True, cwd=wt, capture_output=True, text=True, encoding="utf-8", errors="replace")
        subprocess.run(["git", "-C", wt, "add", "-A"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        subprocess.run(["git", "-C", wt, "commit", "-m", f"build {owner}"],
                      capture_output=True, text=True, encoding="utf-8", errors="replace")  # 無變更時 commit 失敗無妨

    reports = orch.build_with_worktrees(str(repo_root), batches, build_fn)
    conflicts = [b for r in reports for b in getattr(r, "conflicts", [])]
    ok = all(getattr(r, "ok", False) for r in reports)
    return {"status": "built" if ok else "merge_conflict",
            "batch_count": len(batches), "conflicts": conflicts}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="CodexAutoAI Phase 5 建置 gate + Phase 4.5 屬性測試")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--manifest", required=True); p.add_argument("--run-id")
    g = sub.add_parser("gen-tests"); g.add_argument("--spec", required=True)
    g.add_argument("--out", default="tests/test_properties_generated.py")
    b = sub.add_parser("build"); b.add_argument("--manifest", required=True)
    b.add_argument("--repo-root", required=True); b.add_argument("--build-cmd", required=True)
    b.add_argument("--run-id")
    g2 = sub.add_parser("gate", help="Phase 5 每批後的便宜語法 gate（零 LLM）")
    g2.add_argument("--files", nargs="*", help="要檢查的檔案（相對專案根或絕對路徑）")
    g2.add_argument("--manifest", help="改由 manifest 取檔案清單")
    g2.add_argument("--batch", type=int, default=None,
                    help="只檢查第 N 批的 owner_file（**從 1 開始**，需搭配 --manifest）")
    g2.add_argument("--run-id")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "plan":
            out = cmd_plan(args)
        elif args.cmd == "gen-tests":
            out = cmd_gen_tests(args)
        elif args.cmd == "gate":
            out = cmd_gate(args)
        else:
            out = cmd_build(args)
    except Exception as exc:  # noqa: BLE001 — fail-safe
        print(json.dumps({"status": "error", "reason": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False))
        return 0
    print(json.dumps(out, ensure_ascii=False))
    return 3 if out.get("status") in ("escalated", "merge_conflict", "refused", "syntax_error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
