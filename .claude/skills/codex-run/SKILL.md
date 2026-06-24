---
name: codex-run
description: 調用 OpenAI Codex CLI 執行任務。當使用者要求透過 Codex 執行程式碼生成、修復、重構等任務時使用。
---

# 調用 Codex

使用 Codex CLI 執行使用者指定的任務。

## 調用指令

```bash
codex exec --full-auto "$ARGUMENTS"
```

將使用者的指令作為 `$ARGUMENTS` 傳入。

## 執行前確認

在調用前，先確認環境是否就緒：

1. 讀取工作目錄下的 `init.md`，確認包含「CODEX 環境已存在並驗證可以使用」。
2. 如果 `init.md` 不存在或未通過驗證，先執行 `/codex-env-check` 完成環境檢查。

## 檔案路徑約束

**重要**：所有傳給 Codex 的 prompt 必須遵守專案資料夾結構：

- **程式碼檔案**：必須寫入 `src/` 目錄下
- **測試檔案**：必須寫入 `tests/` 目錄下
- **文件**：必須寫入 `docs/` 目錄下
- **不可**在專案根目錄建立程式碼檔案

在 prompt 中加入路徑提醒：
```
重要：所有程式碼檔案必須寫入 src/ 目錄下，測試檔案寫入 tests/ 目錄下，不可寫在專案根目錄。
```

## 執行步驟

1. 在工作目錄下執行：

```bash
codex exec --full-auto "使用者的指令\n\n重要：所有程式碼檔案必須寫入 src/ 目錄下，不可寫在專案根目錄。"
```

2. 等待 Codex 執行完成，將結果回報給使用者。
3. **檢查 Codex 是否將檔案寫在正確位置**：
   - 如果寫在根目錄 → 移動到 `src/` 下
   - 如果寫在其他錯誤位置 → 移動到正確位置
4. 如果 Codex 因沙箱限制無法寫入檔案，由 Claude 接手將產出的程式碼寫入對應的 `src/` 目錄。

## 範例

```bash
# 生成程式碼（注意指定 src/ 路徑）
codex exec --full-auto "寫一個 Python 的費波納契數列函式，存成 src/fibonacci.py"

# 修復 bug
codex exec --full-auto "修復 src/main.py 中的錯誤"

# 重構程式碼
codex exec --full-auto "將 src/utils.js 重構為 TypeScript，存到 src/utils.ts"
```

## 注意事項

- 使用 `--full-auto` 模式，沙箱權限為 `workspace-write`，Codex 可直接讀寫工作目錄。
- 如果未使用 `--full-auto`，沙箱為 `read-only`，Codex 無法寫入檔案。
- 超時設定建議 120 秒以上，複雜任務可能需要更長時間。
- **每次調用後務必確認檔案寫入位置是否正確**。
