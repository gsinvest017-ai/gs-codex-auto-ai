# Astra → Opus → Sol 的微型接線驗證

共同需求：只執行這張明確保存的三節點圖，不啟動七階段流程、不遞迴派工、不呼叫其他 CLI 或 subagent。

1. Codex `gpt-6-astra`，任務節點：建立 `plan.txt`，內容精確為 `Build result.txt containing GRAPH_CHAIN_OK.`；不要建立 result.txt。
2. Claude `opus`，任務節點：讀取 plan.txt 與前步交接資料，建立 `result.txt`，只有一行 `GRAPH_CHAIN_OK`。
3. Codex `gpt-5.6-sol`，唯讀審查節點：讀取兩個檔案，確認 result.txt 去除首尾空白後等於 GRAPH_CHAIN_OK；正確才回覆 `VERIFIED_GRAPH_CHAIN_OK`，不修改檔案。

兩条連線均為前節點成功才啟動。每節點最多一次 provider attempt，沒有備援邊；模型不可用或額度耗盡應如實失敗，不另換模型。

先執行 `python testcase/live_custom_graph.py` 查看計畫；明確授權後才執行 `python testcase/live_custom_graph.py --execute`。腳本使用全新 Desktop 目錄與框架快照，每次 attempt 最多 85 秒、整體最多 300 秒，保留 app-run、events、graph-result、原始 provider 輸出及 live-custom-graph-report。

核對實際呼叫的 provider/configured_model 順序與三個節點一致、前步最後回覆進入後步 prompt、result.txt 精確內容，以及 Sol 的唯讀驗證回覆。actual_model 沒有回報時維持未知，不用 configured_model 冒充。再比對 Workbench 與正式 Dashboard metrics，瀏覽器驗證獨立列示；圖完成不代表七階段交付。

此檔是獨立微型驗證，不加入既有分類矩陣 cases.json。
