# Scenario Wiring Studio：0.16 設計與驗收

## 使用者需求

保留 PR #78 的舊路由與工作台，新增 Claude 優先、指定 OpenCode 模型優先、Codex 審查／Claude 實作方案。每個場景可新增、刪除、拖移節點及接線，編輯名稱、任務指示、模型與條件，儲存後必須由執行器讀取相同設定。需求欄灰色提示依 testcase 與目前選擇變動，不覆寫使用者輸入。

## 通訊邊界

跨服務以明確交接資料傳遞任務與前步結果，各 provider 的 session 身分分開紀錄。不同服務的 session ID 並不共用命名空間；任意正在執行的 Desktop session 不等於可被外部注入訊息。

官方能力核對（2026-09-09）：
- Codex App Server 提供 thread/start、thread/resume、thread/fork 與 turn/start：https://learn.chatgpt.com/docs/app-server
- Claude 非互動模式提供 --resume session ID：https://code.claude.com/docs/en/headless
- OpenCode Server 提供 session message 與 prompt_async：https://opencode.ai/docs/server/

本機 Codex/Claude help 已核對，當前 shell PATH 無 opencode。這不等於其他 shell、Desktop 或遠端未安裝。不能將受控 adapter 測試宣稱為三家真實服務交接成功。

## 驗收重點

1. 舊設定沒有 opt-in 時，仍要求 Codex 與 Claude 兩者明確額度耗盡才用 OpenCode。
2. 指定 OpenCode 優先必须有完整 provider/model，畫面與儲存設定都表達這是明確主線選擇。
3. 儲存後重載，節點位置、文字、model、edges、scenario 不丟失。無效 graph 必須拒絕，不可靜默畫成有效。
4. 任務啟動採用明確場景／圖設定，原始 prompt 與 snapshot 可追查；編輯下一次設定不應改變已啟動任務。
5. 執行紀錄含每步 provider、模型、父任務、節點與交接來源；未知模型或用量不補零。
6. Review→build 的交接必須真傳前步輸出；分支、quota 條件、錯誤終止不得只靠畫線或 LLM 自述。
7. graph 完成與七階段完成分開，不能為了漂亮進度條偽造 phase 事件。
8. 切换 placeholder 不變更需求 value；手動輸入及未儲存編輯不被輪詢或 catalog 更新蓋掉。
9. 插入引號、HTML、shell 字元、路徑與很長文字，必須維持文字資料，不執行成 shell/HTML。
10. 安裝 VSIX 的 framework 與 testcase 資源須與 repo 同版，不能只有開發目錄可用。

## 交付前反例清單

- 儲存 invalid/cyclic/unreachable graph：原檔位元組不變。
- Graph 清空備援模型、OpenCode 主線漏模型：明確錯誤，不改用另一模型。
- 編輯新任務需求後，poll/catalog/graph reload 不重設文字。
- review 節點不能寫檔；provider 不提供可強制唯讀能力時拒絕該角色。
- 舊 quota policy、explicit preset、graph opt-in 各自有 scope，不可用一個全域開關混在一起。
- 前步一般錯誤不走 quota edge；不存在／空產物不算成功。
- 交接包含真結果位置及可取得的最後回覆；未回報的 session ID 維持 null。
- Graph wrapper 結果不是額外模型呼叫，不能重算用量。
- 儲存後再改圖造成 digest 不符時，拒絕舊預覽啟動，而非畫面與執行不同。
## 使用方式

- 一般七階段任務仍由上方「套用預設方案」及「啟動新任務」使用。
- 「Codex 審查 → Claude 實作」也會建立可編輯的圖；在工作室選取該圖，輸入需求後按「執行已儲存接線」。
- 新增接線，設定中文名稱與場景 ID；以滑鼠拖移節點，按「接線」再點來源與目標。選取節點可改角色、provider、model、文字與必要產物；選取線可改標籤及條件。
- 儲存後執行，未儲存圖不能啟動；執行中不能修改或刪除保存設定。圖形 ID 的版本指紋不同時拒絕啟動。
- OpenCode 優先欄位填完整 provider/model；切換到其他方案會清除它，避免誤送給 Claude。多段 provider/org/model 也允許。
- 灰色提示來自 testcase 原始檔，依場景與參數變化；使用者已輸入文字保持不變。

## 此版範圍

可分支、單一來源的有向無環圖，序列執行；不支援循環、多來源合流或任意既有 Desktop session 的即時注入。OpenCode task 節點可用；未有可驗證唯讀限制的 OpenCode review 會拒絕。Graph 完成只代表所選節點與必要產物檢查完成，不等於七階段交付。

## 實際畫面驗收

在隔離的可寫 fixture（禁止呼叫模型）用真瀏覽器完成：中文名稱、Codex review→Claude task、節點任務/產物、連線、滑鼠拖移（Claude x=470,y=52）、保存與重載；原始 JSON 與 DOM 座標一致。另驗證 OpenCode 指定模型顯示不會仍畫成額度備援，以及孤立節點儲存被拒。連續 preset 切換發現模型殘留後，已加入正式 webview→Python 回歸並修正。

## 驗證結果（2026-09-09）

- 全套 Python：1329 passed、4 skipped、2 subtests passed（196.95 秒）。其後最後修正再跑相關組：111 passed；狀態／歷史／hook 組 147 passed、1 skipped。這些計數重疊，不相加。
- 最終 Node：43 passed；包含真 HTML webview → host → Python、圖形存取及逐次 preset 切換。
- 受控子行程模擬 provider，實際走 runner、交接檔、事件與 Python/JS metrics；未呼叫付費模型，未宣稱三家真帳號已連通。
- Graph 結果的當次及歷史跨語言反例：錯 run/digest/schema/時間/節點/退出碼會拒絕完成；接線完成始終標示未驗證七階段交付。
- 0.16.0 VSIX 140 entries；47 組原始碼／封裝內容逐位元組一致，無 cache/pyc/test.js；已安裝並核對新版與主要 runtime。
- GitHub OAuth 缺 workflow scope，Node 22 CI 修改保留於本機 dist/workflow-node-tests.patch，未提交 workflow；Node 回歸已本機通過，既有 Python CI 維持。

初版接線支援有條件的明確交接，不提供把任意既有服務 session 強制併成同一對話的功能。此限制與 CLI 是否安裝、登入及模型可用性應分開理解。

## 0.16.1 選取與刪除修復

工作室新增節點／連線選取清單；不用精準點中曲線，也能選取目標、修改屬性與刪除。曲線增加透明點選範圍，刪除按鈕會明示「刪除此連線」或「刪除此節點與相連線」。刪除節點會一併移除相連線；刪除後清空屬性面板，避免繼續編輯已不存在的項目。

接線模式可再按按鈕或 Esc 取消；重新載入圖、選取項目、刪除及收到驗證錯誤時，會清除尚未完成的來源節點選擇。起點收到流入連線而無法儲存時，保留草稿並提示從清單刪除該連線後重試。草稿變更仍須儲存後才影響下一次執行。

本次最終驗證：Node 45 passed；Python scenario_graph 19 passed。真瀏覽器透過正式 Dashboard bridge 呼叫 Python，新增正向連線及回接起點的錯誤連線後儲存，確認出現中文拒絕原因且保留草稿；從清單刪除回接連線後儲存成功，再從清單刪除節點及其正向連線後儲存成功。磁碟上的圖最終只剩 `n1` 與 `edges=[]`，瀏覽器 console errors 為空。

