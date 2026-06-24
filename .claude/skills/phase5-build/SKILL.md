---
name: phase5-build
user-invocable: false
description: "Phase 5：根據架構文件並行派遣 function-builder agent 開發所有 function。"
---

# Phase 5：並行開發

## 執行

1. 讀取 `docs/architecture.md`，提取 function 清單與依賴關係。

2. **分析依賴，分批並行**：
   - 無依賴的 function → 第 1 批，同時啟動多個 `function-builder` sub-agent
   - 依賴第 1 批的 function → 等第 1 批完成後啟動第 2 批
   - 依此類推

3. 每個 `function-builder` sub-agent 接收：
   - Function 規格（FN-xxx 的完整定義）
   - 專案技術選型與檔案結構
   - **檔案路徑約束**：所有程式碼寫入 `src/` 目錄

## 中控驗證

每個 function 完成後，檢查：
1. **檔案存在於 `src/` 下的指定路徑**（不在根目錄）
2. **function 簽名與架構文件一致**
3. 如 Codex 寫錯位置 → 移動到正確位置

所有 function 完成後：
4. **整合確認**：所有 function 都在正確位置，import 關係無衝突

## 產出

- 每個 function：`log/{YYYYMMDD-HHmmss}-phase5-build-{function名稱}.md`
- 整合結果：`log/{YYYYMMDD-HHmmss}-phase5-integration.md`

## 完成條件

所有 function 完成且整合確認通過 → **自動進入 Phase 6（`/phase6-test`）**
