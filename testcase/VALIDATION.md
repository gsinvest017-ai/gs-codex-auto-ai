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
