---
name: phase7-delivery
user-invocable: false
description: "Phase 7：建置專案執行環境、產出類型專屬報告與交付說明。"
---

# Phase 7：環境建置與專案交付說明

## Step 1：建置專案執行環境

在專案根目錄中，幫使用者把環境架設好，讓他拿到專案後可以直接執行。

### Python 環境

```bash
# 確保 requirements.txt 存在且完整
# 建立虛擬環境
if [ ! -d ".venv" ]; then
  uv venv
fi

# 安裝依賴
uv pip install -r requirements.txt
```

### 前端環境（如有 frontend）

```bash
if [ -f "src/frontend/package.json" ]; then
  cd src/frontend && npm install && cd ../..
fi
```

### 驗證環境可用

實際執行一次，確認不報錯：
- Python 專案：`.venv/Scripts/python -c "import sys; sys.path.insert(0,'src'); ..."`
- Web 專案：啟動 server 確認能跑，然後關閉
- CLI 專案：執行 `--help` 確認入口正常

## Step 2：產出類型專屬報告

根據 `docs/requirements-spec.md` 的專案類型，讀取對應的報告模板，用實際執行結果填入數值。

| 專案類型 | 模板路徑 | 說明 |
|---------|---------|------|
| quant-finance | `docs/templates/quant-finance-report.md` | 技術指標報告：CAGR、Sharpe、MaxDD、勝率、PF、MAE/MFE 等完整績效指標 |
| data-science | `docs/templates/data-science-report.md` | 資料分析報告：資料品質、描述統計、Pipeline 執行摘要、視覺化產出 |
| ml-ai | `docs/templates/ml-ai-report.md` | 模型評估報告：訓練過程、分類/迴歸指標、混淆矩陣、推論效能 |
| web-fullstack / web-backend | `docs/templates/web-fullstack-report.md` | Web 交付報告：API 端點清單、前後端契約對照、頁面路由 |

**執行方式**：
1. 讀取對應模板
2. 實際執行程式，取得真實數據
3. 將模板中的 `_______` 替換為真實數值
4. 儲存到 `docs/{專案名稱}-report.md`

**如果專案類型沒有對應模板**（cli-tool、library、desktop-gui、automation）：跳過此步驟。

## Step 3：產出交付說明

向使用者提供人性化的專案交付說明：

1. **專案描述** — 一段話：這個專案是什麼、做什麼用
2. **專案結構** — 實際的 `tree` 格式，標註每個檔案用途
3. **如何執行** — 完整啟動步驟（Git Bash 指令），環境已建好，告知使用者直接執行即可：
   - Python 專案：`.venv/Scripts/python src/main.py`
   - Web 後端：`.venv/Scripts/python -m uvicorn src.backend.main:app`
   - Web 前端：`cd src/frontend && npm run dev`
   - CLI：`.venv/Scripts/python src/main.py {command}`
4. **操作說明** — 程式的操作方式（快捷鍵、指令等）
5. **類型專屬報告** — 如有產出報告，在交付說明中展示完整報告內容
6. **注意事項** — 限制或特殊情況

## 產出

- 類型專屬報告：`docs/{專案名稱}-report.md`（如適用）
- 日誌：`log/{YYYYMMDD-HHmmss}-phase7-delivery.md`
- 直接在對話中向使用者展示交付說明 + 報告

## 完成條件

環境建好 + 報告填入真實數據 + 交付說明呈現給使用者 → **整個流程結束。**
