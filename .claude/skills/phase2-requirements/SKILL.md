---
name: phase2-requirements
user-invocable: false
description: "Phase 2：派遣需求分析 agent，產出需求規格書。"
---

# Phase 2：需求分析

## 執行

啟動 `requirements-analyst` sub-agent，傳入使用者原始需求。

Sub-agent 負責：
- 理解需求完整範圍
- **決定專案名稱**：使用者有指定就用使用者的，沒指定就根據需求自動取一個簡潔的英文名稱（kebab-case，如 `stock-dashboard`、`quant-backtest`），不需詢問使用者
- **判斷專案類型**（web-fullstack / web-backend / data-science / quant-finance / ml-ai / cli-tool / library / desktop-gui / automation / 其他）
- 拆解功能清單（每個功能有輸入、輸出、驗收條件）
- 列出待確認事項

## 中控驗證

收到需求規格書後，檢查：

1. **專案名稱是否存在**（kebab-case 英文，後續命名報告用）
2. **專案類型是否明確標註**（後續 Phase 都依賴此判斷）
3. **功能清單完整性**：每個功能有輸入、輸出、驗收條件
4. **沒有擅自擴充**：只包含使用者明確提出的需求

## 產出

- 日誌：`log/{YYYYMMDD-HHmmss}-phase2-requirements.md`
- 文件：`docs/requirements-spec.md`

## 完成條件

- **有待確認事項** → 暫停，向使用者提問，等回覆後繼續
- **無待確認事項** → **自動進入 Phase 3（`/phase3-architecture`）**
