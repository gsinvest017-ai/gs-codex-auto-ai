---
name: phase0-init
user-invocable: false
description: "Phase 0：專案初始化，建立標準資料夾結構並記錄日誌。"
---

# Phase 0：專案初始化

## 執行

建立標準資料夾結構：

```bash
mkdir -p src tests docs log
```

## 中控驗證

確認以下目錄都存在：
- `src/` — 原始碼
- `tests/` — 測試程式
- `docs/` — 文件產出
- `log/` — 執行日誌

## 日誌

記錄到：`log/{YYYYMMDD-HHmmss}-phase0-init.md`

內容：建立了哪些目錄、是否成功。

## 完成條件

四個目錄都存在 → **自動進入 Phase 1（`/codex-env-check`）**
