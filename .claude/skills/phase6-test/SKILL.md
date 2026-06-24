---
name: phase6-test
description: "Phase 6：環境建置 + 根據專案類型並行執行完整測試。"
---

# Phase 6：環境建置與完整測試

**測試不只是 import 驗證，必須實際執行程式，逐一驗證所有功能的輸入與輸出。**

## Step 1：環境建置（最先執行）

啟動 `test-runner` sub-agent（env 模式）：
1. 建立 `requirements.txt`
2. `uv venv` + `uv pip install -r requirements.txt`
3. 前端 `npm install`（如有）
4. 其他系統依賴直接安裝，不詢問使用者
5. 靜態驗證（語法檢查、import 檢查）

記錄到：`log/{YYYYMMDD-HHmmss}-phase6-env.md`

## Step 2：類型專屬測試（環境完成後並行啟動）

讀取 `docs/requirements-spec.md` 的專案類型，啟動對應的 test-runner agent：

| 專案類型 | 測試內容 |
|---------|---------|
| **web-fullstack** | 啟動 server → curl 每個 API → 前後端契約對照表 → 每個元件/按鈕對應的 API 驗證 |
| **web-backend** | 啟動 server → curl 每個 API → 輸入輸出驗證 |
| **data-science** | Pipeline 端到端 → 每步資料形狀/型別/筆數 → 數值正確性 |
| **quant-finance** | 資料取得 → 策略訊號 → 回測引擎 → 績效指標（報酬率、Sharpe、MaxDD）|
| **ml-ai** | 資料 pipeline → 模型初始化 + forward pass → 推論格式 → 評估指標 |
| **cli-tool** | 每個子命令輸入輸出 → 錯誤處理（無參數、無效參數）|
| **library** | 每個公開 API 輸入輸出 → 邊界條件 |
| **desktop-gui** | 業務邏輯單元測試（logic/）→ GUI 只做 import 驗證 |

## Step 3：Function 單元測試（並行，通用）

不論專案類型，**每個 function** 都要獨立測試：
- 正常輸入 → 正確輸出
- 邊界輸入 → 空值、極端值
- 錯誤輸入 → 不 crash、適當錯誤處理

## 並行策略

| Agent 實例 | 任務 | 前置條件 |
|-----------|------|---------|
| test-runner-env | 環境建置 + 靜態驗證 | 無（最先執行）|
| test-runner-{type} | 類型專屬測試 | 環境建置完成 |
| test-runner-fn-{name} | function 單元測試 | 環境建置完成 |

## Step 4：收集報告數據

測試完成後，確認 test-runner 有收集 `docs/templates/` 模板所需的數據。

根據專案類型檢查：
- **quant-finance**：CAGR、Sharpe、Sortino、MaxDD、勝率、PF、MAE/MFE 等是否都有數值
- **data-science**：資料筆數、描述統計、Pipeline 每步耗時是否都有
- **ml-ai**：訓練指標、評估指標、混淆矩陣是否都有
- **web-fullstack**：API 端點清單、前後端契約對照是否完整

如有缺漏 → 補跑對應測試取得數據。

## 中控驗證

彙整所有測試結果到：`log/{YYYYMMDD-HHmmss}-phase6-summary.md`

## 完成條件

- **有失敗** → 派遣 `function-builder` agent 修正 → 重新測試失敗項目 → 循環直到全部通過
- **全部通過** → **自動進入 Phase 7（`/phase7-delivery`）**
