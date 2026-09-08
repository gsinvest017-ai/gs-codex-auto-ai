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
