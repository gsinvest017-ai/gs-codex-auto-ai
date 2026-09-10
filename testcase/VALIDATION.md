# 0.15.1 測試驗證紀錄（2026-09-09）

## 覆蓋與結果

- 13 個簡單離線 sandbox prompt；每個有限參數組合均對應案例、完整 prompt 與預期產物。
- 1,120 組：非停 2 × 方案 2 × 場景 7 × 主線 2 × 模型類別 4 × OpenCode 備援類別 5。
- 每組測成功、主線額度耗盡、雙主線額度耗盡、一般錯誤，共 4,480 個受控執行情境，63,840 項檢查通過。
- 正式 Python/JavaScript 用量與命令交叉檢查：8,961 通過，0 失敗，1 未驗證（實際非停續跑）。矩陣沒有呼叫付費模型。
- 相關 Python 回歸：162 passed、1 skipped（Windows symlink 權限限制）。

## 真實 3D 案例：未通過整體時限驗收

run ID：`7e4d7b27-a7ce-47f5-a370-232e63dc9cb5`。
本機證據：`C:/Users/User/Desktop/codexautoai-testcase-20260909-141331-b706c6af/log/live-smoke-report.json`。

單一真實 Codex dispatcher，3d_modeling、CLI 預設模型、非停關閉、OpenCode 未設定。產生六個預期檔案；獨立執行產物測試確認 616-byte GLB 的頂點、索引、alignment 與 32×32 PNG 的 CRC/像素均正確。正式 Three.js viewer 在瀏覽器 harness 實際顯示三角形。

Phase 0–7 事件齊全，但 Phase 7 完成緊貼 300 秒期限；runner 尚未写出 task-result 便被測試時限終止。wall_seconds=301.942，timed_out=true，exit_code=1，結果保留 failed。不能以檔案存在或 Phase 7 事件取代交付 envelope，也不能將這次當成成功的完整 live smoke。此案例亦不足以證明完整實際用量；缺少完成回報保留未知。

## 已發現並修正

- 明確清空備援與省略欄位混淆：前者停用，後者保留。
- 無效 OpenCode provider/model ID 應拒絕；一般錯誤不能冒充額度耗盡。
- 舊格式事件只有 run_id 時，控制台與 Workbench 的用量 scope 不一致。
- 測試工具缺框架模組、缺產物仍 completed、截短矩陣誤通過、未檢查真正進度 DOM 等假通過缺口。

## 明確限制

有限模型類別不等於每個任意模型字串真實可用。全部組合驗路由、fallback、用量聚合與 UI 啟動參數；活動、產物、歷史、階段由代表資料與一個真實案例驗證，並非 4,480 次真實生成。非停實際續跑仍 NOT_VERIFIED。瀏覽器 harness 渲染不代表原生 VS Code 自動開窗已做端到端驗證；該部分仍為 VS Code API mock 與手動驗收步驟。

重跑方式與案例見 README.md；受控報告摘要及來源 SHA256 見 reports/matrix/summary.json 與 ui-summary.json。大型逐筆報告保留本機，由腳本重產，不提交 git。

Node 回歸合計 35 passed；真實失敗案例的 UI/Workbench 一致性稽核 21 passed、0 failed、2 unknown（非停模式證據）。UI 清空備援按鈕也已透過 host bridge 到真 Python 回歸驗證。

## 2026-09-10：新增明確主線方案的受控矩陣

本節為新增覆蓋；上方 1,120／4,480 組與 live timeout 紀錄保留為歷史，不能解讀為本次重新執行或已轉為 live 通過。

- 4,200 組參數：非停 2 × 方案 5 × 場景 7 × 主線 3 × 模型類別 4 × 備援類別 5；每組 4 種結果，共 16,800 個受控執行情境。新增 `claude-first`、`opencode-first`、`review-codex-build-claude`，包含不合法設定應拒絕的組合。
- 明確 OpenCode 主線需保存有效 `provider/model`；它的額度錯誤不應偷偷切往其他主線。預設額度備援仍需兩家主線額度耗盡。
- 完整矩陣 Python 回歸 4 passed（31.23 秒）；本次 Node 回歸 47 passed。JS 稽核對來源 `passed` 標記另核對 expected／actual，並逐一比對 Python 與正式 JavaScript metrics、真啟動命令的非停前綴。
- 矩陣本身 255,640 項檢查通過。報告的來源 SHA256 已逐一比對本機 `matrix.py`、案例清單、router、runner 與 events_model；本次 report SHA256 為 `082cd142b68db239f06cc03d82305878c093e619c9eabd3653f22216270f3328`，亦記錄於 `reports/matrix/ui-summary.json`。
- 本次正式 JS 矩陣稽核為 33,601 passed、0 failed、1 unknown：16,800 項 metrics、16,800 項命令前綴與 1 項來源檢查。未知項目仍是實際非停續跑；這些不是額外的真實模型任務。

本輪未呼叫付費模型，未重新執行上述 live 案例，也不宣稱 Gemini／DeepSeek 或任意測試模型 ID 已實際連通。新版逐筆報告由矩陣工具重產，應與本次 dimensions／coverage 一起核對。
