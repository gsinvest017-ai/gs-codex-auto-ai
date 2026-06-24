---
name: phase4-review
user-invocable: false
description: "Phase 4：派遣 Codex 審查 agent，中控複審架構與需求契合度。"
---

# Phase 4：Codex 審查 + 中控複審

## Step 1：派遣 Codex 審查

啟動 `codex-reviewer` sub-agent，傳入：
- `docs/requirements-spec.md`（需求規格書）
- `docs/architecture.md`（系統架構文件）

等待回傳「審查報告」。

## Step 2：中控複審（批判性思維）

收到審查報告後，Dispatcher **自行以批判性思維複審**：

1. **不可以變多**：有沒有多餘 function 超出需求範圍？
2. **不可以變少**：有沒有遺漏需求對應的 function？
3. **Codex 審查結論是否合理**：有沒有遺漏或誤判？
4. **介面設計是否合理**：參數與回傳值是否正確
5. **檔案路徑**：是否都在 `src/` 下

## 產出

- Codex 審查日誌：`log/{YYYYMMDD-HHmmss}-phase4-codex-review.md`
- 中控複審日誌：`log/{YYYYMMDD-HHmmss}-phase4-dispatcher-review.md`

## Step 3：有界 review-fix 迴圈（Python 擁有迴圈；reviewer≠fixer 用兩個 Codex 模型）

審查不通過時，**不要**自行循環。改為呼叫 `tools/run_loop.py`，由 `Orchestrator.run_fix_loop`
擁有 while + 三守衛；reviewer 與 fixer 用**不同 Codex 模型**以滿足 REVIEW-R1 跨模型審查：

```bash
python tools/run_loop.py --mode review --phase 4 --run-id <id> --max-iters 3 --patience 2 \
  --reviewer-model <模型A> --fixer-model <模型B> --available <A,B> \
  --review-cmd 'codex exec -m <A> --full-auto "逐行比對 docs/architecture.md 與 docs/requirements-spec.md，把每個問題以 TYPE:ID 寫到 {review_out}（TYPE∈MISSING/EXTRA/MISMATCH，ID 用 FN 編號）。"' \
  --fix-cmd 'codex exec -m <B> --full-auto "依 {review_out} 的問題清單修正 docs/architecture.md。"'
```

讀 stdout JSON `status`：`resolved*` → 通過 → **進入 Phase 5**；`escalated`/`error` → 升級終態通知使用者。

## 完成條件

- 由 run_loop 的 `status` 決定（**非 LLM 自判**）。`resolved*` → **自動進入 Phase 5（`/phase5-build`）**。
