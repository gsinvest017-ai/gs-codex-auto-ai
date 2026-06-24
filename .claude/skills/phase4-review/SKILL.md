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

## 完成條件

- **不通過** → 修正 `docs/architecture.md` → 重新派遣審查，循環直到通過
- **通過** → **自動進入 Phase 5（`/phase5-build`）**
