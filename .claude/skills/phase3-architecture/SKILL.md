---
name: phase3-architecture
description: "Phase 3：派遣架構規劃 agent，產出系統架構文件與 function 拆解。"
---

# Phase 3：系統架構規劃

## 執行

啟動 `architecture-planner` sub-agent，傳入 `docs/requirements-spec.md`。

Sub-agent 負責：
- 根據專案類型選擇架構模式
- 將系統拆解為獨立 function（職責單一、可獨立開發與測試）
- 定義每個 function 的介面（輸入參數、回傳值、型別）
- 分析依賴關係，規劃並行分批
- 定義測試策略（供 Phase 6 使用）

## 中控驗證

收到系統架構文件後，檢查：

1. **架構模式與專案類型匹配**（不要把資料科學設計成 web 架構）
2. **Function 與需求一一對應**：不多不少
3. **每個 function 有完整介面定義**：參數名稱、型別、回傳值
4. **依賴關係合理**：並行分批計畫可行
5. **檔案路徑都在 `src/` 下**
6. **每個 function 附帶測試方式**

## 產出

- 日誌：`log/{YYYYMMDD-HHmmss}-phase3-architecture.md`
- 文件：`docs/architecture.md`

## 完成條件

驗證通過 → **自動進入 Phase 4（`/phase4-review`）**
