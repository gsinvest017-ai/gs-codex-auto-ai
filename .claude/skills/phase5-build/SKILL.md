---
name: phase5-build
user-invocable: false
description: "Phase 5：根據架構文件並行派遣 function-builder agent 開發所有 function。"
---

# Phase 5：並行開發

## 執行

1. **確定性批次計畫 + 循環拒絕（ORCH-R6，不要用 LLM 自行推依賴）**：

   ```bash
   python tools/run_build.py plan --manifest docs/fn-manifest.json --run-id <id>
   ```
   讀 stdout JSON：
   - `status=planned` → `batches` 即批次順序（同批內 owner_file 互斥可並行）
   - `status=escalated`（`reason=dependency_cycle`）→ **立即升級終態通知使用者，不得進入開發**

2. **生成 EARS 屬性測試（REVIEW-R3，Phase 4.5 gate）**——讓 Phase 6 能驗證需求條件：

   ```bash
   python tools/run_build.py gen-tests --spec docs/requirements-spec.md --out tests/test_properties_generated.py
   ```

3. 依 `batches` 順序，對每批同時啟動多個 `function-builder` sub-agent。每個接收：
   - Function 規格（FN-xxx 完整定義）、專案技術選型與檔案結構
   - **檔案路徑約束**：依 ownership 寫入分配的 `src/` 目標檔案，不碰其他 builder 的檔案
   - （opt-in 隔離建置：改用 `run_build.py build --repo-root <獨立目標專案>`，見 `function-builder.md` BUILD-R1）

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
