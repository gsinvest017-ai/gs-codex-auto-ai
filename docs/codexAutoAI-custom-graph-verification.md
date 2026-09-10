# 自訂三節點接線驗證

2026-09-10 唯讀查閱使用者專案 `codexautoai-sandbox/log/vscode-sessions.jsonl`：run `680fb8e6-46b9-4b68-b75e-782e9b218836` 的保存路由是 `3d_modeling`、Codex `gpt-6-astra`、`routing_policy=both-primary-exhausted`，沒有 `route.mode=graph` 或 graph ID。結束原因為 `verified_phase7_delivery`。這證明該次啟動走原七階段任務，不能把它當成 Astra → Opus → Sol 的接線執行證據。

回歸 `tests/tools/test_custom_graph_chain.py` 使用三個受控 Python 子行程替代 provider，仍走正式 runner、argv、交接檔與事件。它驗證模型順序、前步回覆傳入後步、Astra／Sol 審查的唯讀 sandbox、Workbench 活動與正式 JavaScript 完整 metrics 一致，並確認 graph 結果不生成 Phase 7 交付 envelope。此回歸不證明實際模型可用。

正式 runtime 對明確保存的 graph／primary 加入當次指令：舊 Codex-only 內容分工屬預設策略，這次應遵守已保存的 provider/model；保留 sandbox 與 hook。Graph worker 只做所屬節點，由框架負責順序與交接，不自行再開七階段或代理。

微型 live 驗證步驟與原始需求見 `testcase/prompts/custom-astra-opus-sol.md`。Live 結果以獨立 sandbox 的 `log/live-custom-graph-report.json` 為準；本段不預先宣稱成功、真實模型名稱或 UI 已通過。

## 2026-09-10 單一 live 微型接線結果

本次授權的獨立 run 為 `custom-live-c8832b2a83c24b6bafa4769d9f105c64`，證據保存在 `C:/Users/User/Desktop/codexautoai-custom-live-c8832b2a83c24b6bafa4769d9f105c64/log/live-custom-graph-report.json`。總時間 61.09 秒、退出碼 0；三節點各一次 attempt，沒有備援重派。Astra 寫入 plan.txt、Opus 依計畫寫入單行 GRAPH_CHAIN_OK 的 result.txt、Sol 唯讀檢查並回覆 VERIFIED_GRAPH_CHAIN_OK。六項腳本檢查均通過。

| 節點 | 實際 CLI provider | configured_model | input / output / cached input |
|---|---|---|---|
| plan | codex | gpt-6-astra | 55504 / 141 / 40576 |
| build | claude | opus | 6 / 882 / 67145 |
| verify | codex | gpt-5.6-sol | 81005 / 351 / 53504 |

三次 CLI 均未回報 actual_model，維持 null；上述是 argv 與事件記錄的設定名稱，不宣稱已獨立確認服務端實際模型。用量為各 CLI 原始欄位，cache 不額外加進 input；不同 provider 的 input/cache 定義不應混作同一計費口徑。

真 live 報告的 Python Workbench metrics 與正式 Dashboard computeState 的完整 JSON 比對通過；graph 結果標記 `task_delivery_verified=false`，不冒充七階段交付。此比對是資料層，尚不代表瀏覽器渲染已驗，原報告仍保留 `ui_verified=false`。受控三節點與 parity 回歸共 41 passed（7.16 秒），不與上述三次 live 呼叫混算。

## 外部瀏覽器 harness 驗收

主線以唯讀 live 目錄 fixture（8797）開啟正式 Dashboard，確認 Codex 彙總 2 次、input/output 136509/492，Claude 1 次、6/882。展開明細為三列，設定模型依序 gpt-6-astra／opus／gpt-5.6-sol，input/output 分別 55504/141、6/882、81005/351，實際模型均顯示未知。活動可辨識「實作 Opus · claude · 設定 opus」與「驗證 Sol · codex · 設定 gpt-5.6-sol」；接線結果列 plan/build/verify 成功，明示非七階段交付。

另一個使用者圖副本 fixture 驗證 self-def 圖的 SaveBind：保存綁定後，本次執行選單自動選定；場景、本次摘要與預覽三區均呈現相同三節點及 success 連線，沒有錯畫舊版額度備援。

上述為正式 Dashboard 的真瀏覽器 harness 驗收，不是原生 VS Code 自動開窗驗證。原 live 報告的 `ui_verified=false` 原封保留；外部驗收另存同 run 的 `log/browser-harness-verification.json`，不改寫原始執行證據，也不增加模型呼叫。

## 0.17.0 使用與交付驗證

0.17.0 VSIX 已安裝。使用者先執行 Reload Window，再於「本次任務場景」選取 `self-def`，確認摘要為 Astra → Opus → Sol 後按 Start。原本的場景規則編輯選單仍是設定編輯用途，與本次執行選擇分開。

原使用者專案的 graph/config 已先備份，備份後綴為 `1789021347751067900`；其後將 self-def 圖的 Astra／Sol 改為完整模型 ID `gpt-6-astra`／`gpt-5.6-sol`，並綁定 `self-def` 場景。此次沒有重新啟動使用者原本的 3D 任務；上方 live 證據只來自獨立微型 sandbox。

交付檢查：全套 Python 1358 passed、4 skipped、2 subtests passed（188.24 秒）；最後 Node 48 passed；graph_edges 最後相關組 27 passed。相關組與全套可能重疊，不相加。0.17.0 VSIX 共 140 entries，31 組 runtime 原始碼／封裝內容比對通過，並已安裝。
