---
name: codex-env-check
description: 檢查 Codex CLI 環境是否設定完成。Claude 每次被觸發時應最先執行此檢查。
user-invocable: false
---

# Codex 環境檢查

執行以下檢查流程：

## Step 1：檢查 init.md 是否存在

讀取工作目錄下的 `init.md`。

### 如果 init.md 不存在

→ 跳到 Step 2 執行完整環境驗證。

### 如果 init.md 存在

→ 讀取內容，檢查是否包含「CODEX 環境已存在並驗證可以使用」。
- **包含** → 檢查通過，告知使用者環境正常，結束。
- **不包含** → 跳到 Step 2 執行完整環境驗證。

## Step 1.5：確認專案資料夾結構

確保標準資料夾存在（Phase 0 初始化）：

```bash
mkdir -p src tests docs log
```

## Step 2：Codex 環境驗證

依序執行以下檢查：

### 2.1 確認 Codex CLI 已安裝

```bash
codex --version
```

- 失敗 → 嘗試執行 `npm install -g @openai/codex`，如仍失敗則**暫停，提示使用者參照 `README.md`「事前準備」安裝 Codex CLI。**

### 2.2 確認已登入

```bash
codex login status
```

- 失敗 → **暫停，提示使用者參照 `README.md`「事前準備」完成 Codex CLI 登入，等使用者確認後再繼續。**

### 2.3 確認工作目錄是 git repo

```bash
git rev-parse --is-inside-work-tree
```

- 失敗 → 執行 `git init`。

### 2.4 實際調用 Codex 確認可用

```bash
codex exec --full-auto "回覆 hello"
```

- 成功 → 進入 Step 3。
- 失敗 → 根據錯誤訊息排查 Step 2.1-2.3 的問題，**提示使用者參照 `README.md` 排查，並參考「常見問題」。**

## Step 3：寫入 init.md

驗證全部通過後，建立或更新 `init.md`，寫入以下內容：

```
# 環境初始化紀錄

## CODEX 環境
狀態：CODEX 環境已存在並驗證可以使用
驗證時間：{當前日期時間}
版本：{codex --version 的輸出}
```

告知使用者 Codex 環境檢查完成，可以正常使用。
