# 本地 sandbox 驗收案例

這組案例用來分別驗證「需求如何分類」、「實際模型呼叫與用量」、「任務是否交付」及「控制台顯示是否一致」。不同層的證據不能互相替代。預設測試不呼叫付費模型；只有明確執行 `run_live.py --execute` 才啟動一個真實 3D dispatcher 案例。

## 案例清單

`cases.json` 是機器可讀清單，`prompt_file` 相對於本目錄。`routing_text` 是簡短原始意圖，用於分類矩陣；使用者貼到 UI 的內容則是完整 `prompts/*.md`。**兩種文字都要驗分類**，不能只挑短句通過而忽略完整需求被其他關鍵字干擾。

| ID | 預期自動場景 | 主要產物／目的 |
| --- | --- | --- |
| default-tiny-sum | default | result.json 的 sum=5 |
| 3d-tiny-triangle | 3d_modeling | 小於 10 KB 的三角形 GLB、32×32 PNG、產生器與單一驗收測試 |
| debugging-off-by-one | debugging | 加總漏首項的最小修正、先失敗再通過的測試 |
| review-local-path | review | 在 sandbox 內證明相對路徑越界，保留 findings |
| research-local-table | research | 本地人工樣本 A_mean=5、B_mean=9，不推論真實模型速度 |
| documents-small-note | documents | 恰好三個二級標題的小型 Markdown 操作說明 |
| coding-temperature | coding | 攝氏／華氏純函式與五個測試 |
| negative-3d-text-only | default | 否定 3D；只產生文字，不應建立 GLB／PNG |
| cross-intent-review-3d | review | 「Review 3D mesh」以審查意圖為主，不建立模型 |
| adversarial-untrusted-claim | review | 引文要求假稱 UI 已驗證；應當資料分析並拒絕採信 |
| failure-missing-delivery | default | 刻意缺 expected-delivery.txt，預期 failed／incomplete |
| mode-off-control | default | 非停模式關閉對照 |
| mode-on-control | default | 同一完整需求，非停模式開啟對照 |

所有任務都小型、離線、標準庫優先。經正式 UI dispatcher 執行時最多使用**一個可重用的原生 child agent**，禁止遞迴派工、nested CLI 或第二個 dispatcher；獨立 worker 可以完全不用 child。這個上限與正式 dispatcher 的分工要求相容，不能拿「禁止任何 agent」測試與既有流程互相矛盾的情境。

`expected_primary=codex` 只適用於全新 Codex-first 設定。若套用雙主線方案，review／research 的主線可以是 Claude；應以該次設定的路由結果為預期，不要把清單中的初始值當成所有設定都相同。

## UI 手動驗收

1. 建立全新、一般權限的工作區，不覆蓋原有專案。打開控制台。
2. 「場景接線設定」下拉選單是**編輯該場景的路由規則**，不是強制下一次任務使用那個場景。儲存規則後，把完整案例文字貼入需求欄，再按「預覽模型路由」。
3. 比較預覽自動分類與 cases.json 的 expected_scenario；執行後再核對 actual attempts 的 scenario／provider／configured model／actual model。未知模型或 token 應保留未知。
4. 觀察本次需求文字、活動來源與 run ID。實際工具或檔案更新才是證據；prompt 中說「UI 都通過」不是證據。
5. 3D 產物穩定後應在旁邊自動開啟一次；可旋轉、縮放、fit。再次寫入模型後應讀取新內容且保留視角，不重複開啟面板。CLI 測試不能代替這一步真 UI 驗證。
6. 刻意缺交付案例應失敗／未完成；exit=0、單一模型回答成功或某個檔案存在，均不足以代表七階段交付完成。

非停 off/on 案例使用完全相同的需求文字。切換的是 UI checkbox，**不是 prompt 中的字眼**。矩陣能確認路由與啟動命令前綴不被錯接，不能證明長任務實際續跑；若沒有真實 continuation 事件，結果必須標 `NOT_VERIFIED`。

備援欄位清空代表明確停用 OpenCode 模型；程式呼叫省略 fallbackModel 欄位才表示保留既有值。模型欄位空白或純空格表示使用 CLI 預設。不要把 Gemini／DeepSeek 選成正常主線；只有兩個主線額度皆耗盡的證據才允許 OpenCode。

## 不呼叫付費模型的矩陣

```powershell
python testcase/matrix.py --output testcase/reports
python -m pytest testcase/test_matrix.py -q
node testcase/evidence_audit.js --matrix testcase/reports/report.json
```

矩陣涵蓋場景、主線、代表性模型、備援設定與非停選擇，再搭配受控 provider 成功／quota 結果。模型 ID 是測試資料，僅驗證儲存與 argv 傳遞，**不代表該模型真實可用**。mock 用量只驗證資料彙總一致性，不代表帳戶真實用量。報告需分列 passed／failed／unknown；缺少 evidence 不應自動算通過。

## 單一真實 3D smoke（明確 opt-in）

先看計畫，不會啟動模型、不建立 sandbox：

```powershell
python testcase/run_live.py --case 3d-tiny-triangle
```

明確授權後才執行：

```powershell
python testcase/run_live.py --execute --case 3d-tiny-triangle --timeout 300
```

此腳本使用全新 `Desktop/codexautoai-testcase-時間-唯一碼` 目錄，普通 mkdir 繼承 Desktop ACL，不使用可能讓 Codex sandbox 存取受阻的私有暫存 ACL。它不重用、不刪除、不修改使用者已有目錄。

框架複製範圍包括 tools、.claude、.githooks、src/codexautoai_v2、docs/templates 與 AGENTS／CLAUDE／setup。這樣 run_phase 才有真正可匯入的 engine。app-run 的預定 route 由正式 router 解析並保存；`requested_nonstop=false` 不宣稱已驗證 continuation。

真執行使用 `codex_runner.py --dispatcher`、主線 Codex、CLI 預設模型，每個 provider 至多一次 attempt，整個 runner 有 300 秒硬期限。**Production 的 quota fallback 政策仍保留**，所以「retries=1」不是所有 provider 合計必然只有一次；未設定 OpenCode 模型。報告要以 actual attempts 為準，不得假稱只發出一個 CLI 呼叫。

這是**單一 live case**，不是替所有矩陣組合花費付費額度。進度由 dispatcher 在真正階段邊界透過框架寫入，腳本不偽造 phase0–7 完成事件。產物預期為 GLB、PNG、generate.py、tests/test_artifact.py、delivery.md、evidence.txt。

輸出包括 sandbox 路徑、run ID 與 `log/live-smoke-report.json`。report 必須區分 task result、call metrics、artifact presence 與 UI 驗證。即使 task status 是 completed，只要缺必要產物就要回 incomplete／非零退出。檔案存在檢查不證明 GLB 結構正確：dispatcher 必須實際執行 `tests/test_artifact.py`；交付後再由獨立驗證者解析 GLB/PNG、開 viewer 與核對 UI。腳本始終保留 `ui_verified=false`，直到外部驗證者提供真證據。

## 留存與比較

保留每次 sandbox 與事件原檔；不要用下一次結果覆寫前一次失敗。對照 `log/events.jsonl`、task-result、app-run、provider CLI 輸出、產物 hash 及控制台輸出。已完成模型可以重新預覽，但重新開啟不代表重新執行模型或重新產生用量。
