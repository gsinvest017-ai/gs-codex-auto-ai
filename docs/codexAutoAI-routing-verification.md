# 模型調度與 UI 一致性驗證紀錄

## 驗證範圍

本次驗證涵蓋本機 CodexAutoAI 的主線模型選擇、額度耗盡備援、worker 寫入權限、場景設定保存，以及 UI 與實際執行事件的 metrics 一致性。主線為 Codex / Claude；OpenCode 僅在同一 runner invocation 取得兩個主線明確額度耗盡證據後才符合呼叫條件。

## 三個並行專業代理與整合

- 後端代理：共用 router、dispatcher / worker runner、額度錯誤分類、OpenCode adapter、CLI 輸出與 attempt 事件。
- UI 代理：場景與模型接線編輯、儲存與預覽、實際事件呈現、session 用量修正及瀏覽器驗證工具。
- Metrics／獨立審查代理：Python 事件模型、Python / JavaScript parity、備援 writer 的當次授權、反例測試與文件。

主代理負責整合、實際 Codex CLI 呼叫、瀏覽器操作與最終驗證；代理之間直接協調事件欄位與修改範圍。

## 獨立反例審查與修正

1. App、dispatcher 與 worker 原先使用不同 parent ID，UI 依 App ID 篩選時會漏掉真正執行事件。已傳遞並保留同一 parent_run_id；以真實 Python 子程序驗證環境值保留。
2. UI 選擇 CLI 預設模型時原先傳入空字串，Python 拒絕保存。已改為省略模型參數，並以真實 Python router 保存及讀回 catalog 驗證。
3. fallback dispatcher 進入 Phase 0 所發出的 run_start，原先會清掉之前 Codex 額度耗盡證據。已改為只用該邊界選擇預設查詢範圍，同一 run / parent 的完整 attempt 與 quota 證據仍保留。
4. 未知 token 原先被 provider 合計顯示為零。各欄現以 null 表示完全未知，另保留 inKnown / outKnown / cacheKnown / costKnown；不完整合計標示「已知部分」及未知筆數。
5. session token 原先可能把新任務之前的累積量算入，並再次加上 reasoning_output_tokens。現依事件時間取累積值差額，output_tokens 不再重加 reasoning 子集。
6. Claude 備援寫入只授權當次未過期的 writer attempt；必須有同 invocation 前序 Codex quota_exhausted 證據。錯 attempt、錯角色、過期、缺證據或已出現 terminal 均不放行；沒有全域關閉守門。

預覽不計為已執行；started 不計為完成呼叫；同一 run_id + attempt_id 重播不重複加總。各 worker invocation 的額度證據彼此隔離。

## 真實 Codex CLI 驗證

實際成功的 runner invocation：`0b0e65274f40463ab949674a04dc9150`。

| 欄位 | 實際結果 |
|---|---|
| 呼叫次數 | 1 attempt |
| Provider | Codex |
| Input tokens | 27,361 |
| Output tokens | 13 |
| Cached input tokens | 13,056 |
| 實際模型名稱 | CLI 未回報，保留未知 |

初次執行使用舊的 `--full-auto`，本機 Codex CLI 0.153.4 以 exit 2 拒絕該參數。runner 改用 `--sandbox workspace-write` 後，真實呼叫成功。設定的模型名稱不當成 CLI 已回報的實際模型，也沒有藉由這次測試宣稱某個特定模型名稱已驗證。

## 瀏覽器與實際資料核對

主代理在瀏覽器操作場景保存，透過真正的 Python router 將 review 設為 Claude / sonnet，再取得 preview；保存內容與預覽結果一致。這證明政策保存與解析連線，並不代表已對 Claude / sonnet 發出真實模型請求。

同一瀏覽器畫面載入上述真實 Codex attempt 事件，顯示相同 input 27,361、output 13 與 cache 13,056；實際模型名稱維持「未回報」。另將同一份真實 events.jsonl 同時輸入 Python routing_stats 與 JavaScript summarizeRoutingAttempts，完整 JSON 相等斷言通過，包含每欄 coverage。政策預覽、實際呼叫與歷史 session 觀測分開呈現。

另在真實瀏覽器把 3D 場景從原 Astra 設定改為 Claude，模型欄會清空；保存後確認底層設定 model 為 null，不會將 Codex 模型誤傳給 Claude。備援模型未設定時，UI 明示「未設定／不可用」。

## 可重現測試與限制

- 相關 Python 測試批次：102 passed。
- Python / JavaScript 事件與 metrics parity：36 passed。
- JavaScript 測試批次：8 passed。
- OpenCode 在本次環境不在 PATH，沒有完成 Gemini / DeepSeek 經 OpenCode 的實際供應商呼叫驗證。
- 額度耗盡、一般錯誤、成功、備援順序及產物存在檢查，使用假 CLI 的真實子行程輸出驗證。子行程會真正啟動、退出、寫入事件與測試產物；它不使用真實供應商帳號，因此不能代替付費模型的端到端驗證。

整套測試曾在既有 ConPTY 相關測試附近停住；隔離執行 termserver 測試得到 37 passed、1 skipped。其他測試批次曾得到 1,153 passed、2 skipped 與 1 個 parity failure；該 parity 問題修正後，聚焦 parity 36 項全數通過。上述是各次測試觀測，數字有重疊，不可相加作為最終全套測試總數。

## 最終分組全套測試

為隔離前述 ConPTY 停住問題，最終將測試分成互不重疊的兩組：

```powershell
python -m pytest tests/ --ignore=tests/test_termserver.py -q -o faulthandler_timeout=30
python -m pytest tests/test_termserver.py -q
```

| 測試範圍 | 結果 | 時間 |
|---|---|---|
| tests/，排除 test_termserver.py | 1,158 passed、2 skipped、2 subtests passed | 122.32 秒 |
| test_termserver.py 獨立執行 | 37 passed、1 skipped | 44.76 秒 |
| 不重疊合計 | **1,195 passed、3 skipped，另有 2 subtests passed** | — |

最終兩組均通過。本紀錄保留原始全套曾停住的觀測，不把分組通過推定為原始卡住問題已重現並修復，也不把未執行的供應商呼叫寫成成功。


## 0.14.1：非 Git 工作區與正式 UI 讀取路徑

原 sandbox 的三次實際失敗紀錄，原因都是 Codex 啟動時拒絕未受信任的非 Git 目錄。0.14.1 在 Codex argv 加入 `--skip-git-repo-check`，同時保留 `--sandbox workspace-write`；只略過 Git 目錄預檢，沒有改寫全域信任設定、關閉沙箱或調整檔案 ACL。此類 trusted-directory 與無效 CLI 參數錯誤改為啟動階段 fatal，不重試，也不當成 quota_exhausted 開啟備援。

主代理使用 PowerShell New-Item 在原 sandbox 的 `log/startup-selftest-20260908` 建立普通 ACL 的非 Git 測試目錄，真實寫檔驗證成功：

| 欄位 | 實際結果 |
|---|---|
| Run ID | `17929477bbb24016ab4f1ef07141de27` |
| 時間與次數 | 47.8 秒、1 attempt |
| 產物 | smoke.txt，內容精確為 `NON_GIT_WRITE_OK` |
| Input / output / cache tokens | 111,475 / 316 / 92,288 |
| 實際模型名稱 | 未回報，維持未知 |

另一次使用 Python mkdtemp 私有 ACL 目錄的測試未成功；不能以普通 ACL 目錄的成功掩蓋該結果。本次沒有為測試更改全域設定或 ACL。

正式控制台接線由 `wireDashboard` 呼叫 `computeState`，讀取 runner 真正寫入的 `log/events.jsonl`，再按 App 的 parent_run_id 呈現 dispatcher 與 worker attempts。舊 `model-routing-events.jsonl` 僅作相容讀取。測試直接呼叫 Python runner 的 record_attempt 寫檔，再經正式 webview bridge 讀回三次失敗事件與原因，並確認失敗畫面不顯示為仍在執行。

控制台及狀態列快速輪詢均使用 includeHistory=false；測試把 os.homedir 改為拋出錯誤，仍能完成正式 bridge 更新，證明該路徑不掃描歷史 session。歷史區顯示「未載入」，實際用量取自本次 attempt 事件。

獨立審查另重現快速模式把舊任務 Phase 7 完成與錯誤帶入新 App 任務的問題，已以 app-run.started_at 篩選進度事件；實際 Node 重驗新任務不再繼承舊完成或失敗。新增 bridge 測試亦明確設定 Python stdin UTF-8，避免依賴主程序的 PYTHONUTF8 環境設定。

0.14.1 最終相關測試批次為 Python 145 passed（61.12 秒）與 Node 10 passed（414 毫秒）。兩批部分驗證範圍重疊，不相加為另一個全套測試總數。主代理亦以真實工作區唯讀 fixture 經正式快速讀檔 bridge 驗證：GUI 顯示原本的 3 次 failed、未受信任目錄原因與 Phase 失敗狀態，不再顯示 running。這次使用真實既有事件；測試用合成事件與真實供應商呼叫證據仍分開記錄。

## 0.14.2：CLI 呼叫成功與任務交付分離

dispatcher 結束時寫入 `log/task-result-{appRunId}.json`，schema_version=1，包含 run_id、invocation_run_id、started_at、ended_at、status、reason 與 completion_evidence。只有 `completed` 回傳 exit 0；`blocked`、`incomplete`、`failed` 都回傳 exit 1。`model_attempt.outcome=ok` 保留模型呼叫成功的原義，並不聲稱 pipeline 完成，tokens 也不因任務阻塞而歸零。

完成判定要求本次 runner 開始後追加的 Phase 7 success event，run_id 必須匹配本次 parent，而且 artifacts 記載的專案內非空檔案必須存在、SHA-256 必須相符。舊 run、舊 timestamp、舊事件、被改動或遺失的產物不構成完成證據。這是事件與交付檔案一致性驗證，不是對產品品質的獨立證明。Phase failure 會標示 blocked；exit 0 但缺交付證據標示 incomplete。

`run_phase.py` 優先繼承 CODEXAUTOAI_PARENT_RUN_ID，拒絕衝突的 explicit run id，phase_start/end 都附 run_id。啟用 dispatcher 時 Phase 7 `end --status success` 必須提供 `--artifact`；CLI 明確回報驗證錯誤。dispatcher prompt 直接說明 phase bridge 與交付契約，不依賴 Claude 專屬 slash-command hooks 啟動。

新增 `tests/tools/test_dispatcher_completion.py`：13 passed（4.77 秒），包括 stale run/time/offset 排除、hash 變更、Phase failure、parent 衝突，以及兩項真正子行程 fake CLI → runner → task-result 測試（缺交付 incomplete；實際 run_phase 與檔案證據 completed）。這些 fake CLI 測試不使用付費模型，也不代表使用者的 3D 機器人任務已完成。

0.14.2 最終整合驗證：Python 169 passed（64.79 秒），Node 12 passed。Windows 真實 shell smoke 1 attempt 成功，input 44,418／output 546／cache 21,760；實際 PowerShell、Git、Python 均可啟動，測試檔精確為 SHELL_WRITE_OK。主代理另以正式 webview bridge 讀取原使用者工作區並在瀏覽器核對：舊 completed 紀錄显示 incomplete／已停止，任務未完成，原 input 328,567／output 1,769／cache 293,504 原樣保留。驗證未改寫原任務日誌，亦未宣稱已重新完成使用者的 3D 任務。


## 0.14.3：原生代理與 CLI 用量分組

Codex dispatcher 的 CLI 回報與原生子代理 rollout 回報分開呈現。`role=native_worker` 是原生代理紀錄，不視為新增 CLI 呼叫；同一 parent_run_id 下依 run_id + attempt_id 去重。原生代理仍未完成、outcome=started 時，不列入完成紀錄或用量合計。

provider 統計中，attempts 是所有完成紀錄筆數；cliAttempts 與 nativeAgents 分別記錄 CLI 與原生代理筆數。既有 inTok / outTok / cacheTok 只累計 CLI 回報，nativeUsage 保存原生代理用量；兩組在 UI 獨立顯示，不相加成完整團隊總量。每組保留各欄已知／未知筆數，未回報的模型或 token 仍為未知。dispatcher 的 native_agent_usage_verified=false 不代表子代理不存在，僅避免將父 CLI 數值誤稱為已獨立驗證的所有子代理用量。

本次執行原需求摘要與啟動時的預定路由另行顯示，並保留使用者目前正在編輯的需求欄內容，避免把尚未送出的新需求誤認為正在執行的任務。

聚焦驗證：40 個 Python / JavaScript parity 測試及 8 個 dashboard Node 測試通過；包含原生代理重播去重、尚未完成紀錄排除、CLI 與 native 用量不重加、未知 child 模型／用量，以及更新狀態不覆蓋需求編輯內容。這些 fixture 測試不代表真實原生代理 smoke 已完成；真實派工與 rollout 證據另由整合驗證記錄。


### 0.14.3 真實原生代理 smoke 與資料重播

真實 parent thread `01a08469-b5d5-7c22-a11e-21d1b3786581` 派出 child thread `01a08469-e115-79c1-9b34-4e3b4ab0bd5a`。從既有 parent CLI stdout 與 native_usage collector 找到的明確關聯 child rollout 讀取，而非從模型口述推算：

| 來源 | 紀錄類別 | Input / output / cache | 模型證據 |
|---|---|---|---|
| Parent CLI stdout | 1 次 CLI 呼叫 | 89,105 / 512 / 65,920 | 未回報實際模型 |
| Child 自身 rollout | 1 筆原生代理 | 51,783 / 154 / 25,600 | child 自身上下文回報 gpt-6-astra |

另逐位元組重驗 native-worker.txt，內容精確為 `NATIVE_WORKER_OK`。用這兩份真實來源生成隔離 UI 投影事件，並明確標為「真實來源證據重播；不是重新執行」。隔離 replay-manifest 保留來源檔與 thread 關係；沒有寫入原使用者 sandbox，也沒有把新建投影事件冒充當時 runner 原始 model_attempt 歷史。

該重播資料輸入 Python routing_stats 與 JavaScript summarizeRoutingAttempts，完整 JSON 相等斷言通過，並確認 CLI 與 native 各一筆、用量分組且不相加。這項驗證證明真實來源資料的解析與 UI 模型一致；完整多階段任務交付另由整合測試驗證。


### 0.14.3 完整多階段 E2E 交付證據

隔離 E2E 專案 `codexautoai-e2e-0143-reset` 的真實 task-result-e2e-0143-reset.json 回報 completed，原因為 verified_phase7_delivery；task run 為 `e2e-0143-reset`，runner invocation 為 `8e513e25813c4af2b1d2aca23cfac223`。Phase 7 成功證據列出 docs/delivery.md、src/greet.py 與 tests/test_greet.py；獨立重算三個當前檔案 SHA-256，均與交付證據一致。

直接讀取這次 E2E 的原始 events.jsonl，沒有建立替代事件或修改工作區資料，Python routing_stats 與 JavaScript summarizeRoutingAttempts 的完整 JSON 相等斷言通過：

| 真實來源 | 完成紀錄 | Input / output / cache |
|---|---|---|
| Dispatcher CLI | 1 次 CLI 呼叫 | 1,408,450 / 4,387 / 1,348,608 |
| 原生代理自身 rollout | 3 筆原生代理 | 617,688 / 4,299 / 556,672 |

三筆 child 用量分別為 163,123 / 1,109 / 128,512、237,942 / 1,580 / 228,224、216,623 / 1,610 / 199,936。各 child 的實際模型由自身 rollout 回報 gpt-6-astra；parent CLI 未回報實際模型。CLI 與 native 合計維持分組，不相加。成本沒有來源數值，保留未知。

主代理另獨立執行 unittest，2 項測試通過；命令列的 Hello Ada 與 Hello world 行為均驗證成功。此完成結論來自交付事件、產物與測試，不只依據模型呼叫 exit 0 或 Token 回報。

本次 E2E 使用啟動當時的 runtime 與 collector；原始 child 事件仍使用當時的 invocation run_id 與 native_agent_usage scope。最後版本對 native child run_id / scope 的正規化與 guard 修正，由回歸測試覆蓋；沒有改寫原始 E2E 事件，也不把一次先前 E2E 成功宣稱為最後所有增量皆再次完成了真實模型全程執行。

0.14.3 最終回歸：Python 188 passed（67.23 秒）、Node 13 passed。VSIX 驗證包含 native_usage.py 且核心程式與測試來源相符，不包含 Python bytecode。瀏覽器以正式 webview bridge 顯示真實來源重播，CLI 與原生代理兩組數據、未知模型及本次原需求均與事件相符。

## 0.15.0：活動、產物工作台與 MCP 相容層

保留既有啟動與額度路由；新增本機 GLB/glTF viewer、可讀活動摘要、按需歷史、共用 Workbench API 與可選 MCP stdio provider。OpenCode 仍只能在 Codex 與 Claude 的明確額度耗盡證據都成立時使用。MCP 預設五個唯讀工具，不啟動模型或接管 host 的模型選擇。

本輪驗證使用使用者已完成的 run `4c2b1d12-18b0-42c7-be69-ff86937ef69e`，只讀既有事件與產物，沒有重新消耗模型額度跑另一個七階段任務：

- 正式 viewer HTML、Three.js、GLTFLoader 與 readModel 載入 assets/codexautoai-avatar.glb（755,316 bytes、94 meshes、6 materials），瀏覽器實際渲染成功。滑鼠旋轉後按重新載入，模型與視角保留；fit 可操作。
- 自動開啟與更新由 VS Code API mock 驗證：檔案穩定後只建立一個 Beside/preserveFocus 面板；同路徑實際寫入新 GLB revision 後送出新模型內容，不建立第二個面板。原生 VS Code 視窗仍需重載後啟用新版，這項 mock 不冒充原生視窗實測。
- 正式 dashboard bridge 只讀本專案，顯示 GLB、OBJ、PNG 三項產物、最後中文交付回報與活動。歷史按鈕成功載入三個舊任務；ca6ad6cb 顯示任務受阻、最後呼叫成功，兩種狀態分開。
- UI summarizeRoutingAttempts 與 Workbench metrics 的完整 JSON 精確相等，含事件順序：3 次 CLI（已知 input 3,511,858/output 6,014，1/3 覆蓋）、6 筆完成 native records（input 5,099,360/output 38,391）。兩組不重加；未知成本、模型與未回報用量保留未知。
- 真正啟動 MCP stdio 子行程，完成 initialize/initialized、tools/list、run_status、list_artifacts、resources/read；讀到相同 run、completed 任務、三項產物與公開 CLI 活動。預設工具列表僅五項讀取工具，reasoning_exposed=false。
- Watchdog 子行程反例測試：本次 stdout 持續更新或明確關聯 child session 更新可維持正常執行；不相關 parent 的 session 更新不能掩護停滯。啟動事件現在立即包含 result_path，使執行中活動可讀。
- MCP 與 JavaScript taskResult 以 11 種交付資料情境驗證狀態一致；舊 completed 缺證據改為 incomplete，保留 CLI 的成功與用量原義。

核心既有回歸 188 passed；Workbench/MCP 新增組 28 passed、1 skipped（此 Windows 環境無建立 symlink 權限）。兩組測試範圍分列，未宣稱為整個 repository 全套。產物版本登記只保存 metadata 與雜湊，不提供舊 GLB 快照還原。

最後 Node 回歸 26 passed。VSIX 0.15.0 共 123 檔、816,436 bytes；15 項關鍵封裝來源逐位元組一致，無 pyc/__pycache__。已安裝 gsinvest.codexautoai@0.15.0，11 項安裝後來源比對通過。VSIX SHA-256：7869555ebdbea764b52fe300c566a2c9f737663c3568511d9426b22e78f85903。Three.js 原版 vendor 有一處上游縮排空白，保留原始檔案未做格式重寫。
