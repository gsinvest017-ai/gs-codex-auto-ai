# Design: v2 Reliability Overhaul

> 技術方案。回答 proposal 的 "How"。每個決策標註來源與取捨。

## 1. 架構總覽：確定性外殼 + 自治核心

借 **CrewAI Crews/Flows 分離**：把 LLM 的不確定性關進一個確定性的編排外殼。

```
┌─────────────────────── 確定性外殼 (Orchestrator / Flow) ──────────────────────┐
│  狀態機：phase 推進、Gate 判定、三守門終止、拓撲排序、checkpoint              │
│  ── 純程式邏輯，不靠 LLM 決定控制流 ──                                          │
│                                                                                │
│   ┌── 自治核心 (Agents) ──────────────────────────────────────────────┐       │
│   │  Claude 大腦  ·  Codex 寫手  ·  跨模型 reviewer  ·  property-verifier │       │
│   │  ── LLM 不確定性被限制在單一節點內，輸出經 Gate 驗證 ──             │       │
│   └────────────────────────────────────────────────────────────────────┘       │
│                                                                                │
│  事件匯流排：每個 LLM/tool/檔案操作 = 一個 JSONL span (gen_ai.*)              │
│  狀態存儲：run-state.json（action-level）+ 事件日誌（可 replay）              │
└────────────────────────────────────────────────────────────────────────────────┘
                    │ 不可逆邊界 (commit/push/deploy) → 單一 interrupt gate
                    ▼
                  人類
```

**關鍵原則**：控制流（迴圈、分支、終止）由**程式**決定，不由 LLM 自由發揮。LLM 只負責節點內的生成。這直接消滅「Devin 追不可能任務一天」的失控類。

## 2. 七 capability 的關鍵決策

### 2.1 orchestration — 三守門終止
- **決策**：每個修正迴圈（Phase 4 審查、Phase 6 測試）由三個獨立守門包住：
  1. `max_iterations`（預設 3，來自 MetaGPT debug cap）
  2. `no_progress`：對 reviewer/tester 回報的**缺陷集合**取正規化 hash，連續 2 次集合不縮小 = 卡住（來自 ChatDev「2 次無變化」+ LangGraph 官方警告「上限只是讓你燒更多錢」）
  3. `budget`：累計 token/$/wall-clock 上限（來自 AutoGen TokenUsageTermination）
- **取捨**：no_progress 是最關鍵也最易漏的一道——純 max-iter 仍會把整個預算燒在卡住的迴圈上。三者任一觸發 → 路由到 `escalation-handler`，**不死循環**。
- **拓撲排序**：分批前對 FN 依賴圖跑拓撲排序，偵測環 → 立即報錯（修 P0-⑩）。
- **escalation 前最後一搏**：採 Magentic-One stall-counter → 觸發一次「重新規劃」再 escalate。

### 2.2 review — 跨模型 grounded CRITIC
- **決策**：審查分三層（來自 CRITIC / 跨模型對抗 / N-Critics）：
  1. **確定性訊號先行**：先跑測試/編譯/lint，把**結果**餵給 reviewer（CRITIC 錨點）——審查不是審「意見」而是審「事實 + 程式碼」。
  2. **跨模型**：fixer=Codex 則 reviewer=Claude（反之亦然）。共用權重=共用盲點，異質性是重點（修 P0-②）。
  3. **屬性驗證 gate**（來自 Kiro）：把每條需求的 EARS 驗收標準編譯成對生成程式碼的自動斷言，Phase 5 後執行（修 P1-⑤）。
- **新增 Phase 4.5**：code↔spec review，介於 build 與 test 之間。
- **安全 gate**：對 web/auth 類產出跑 secret 掃描 + 基本 SAST（修 P2-⑭）。

### 2.3 parallel-build — worktree 隔離
- **決策**：每個並行 builder 在**獨立 git worktree**（獨立 HEAD/index/working tree）工作，按**檔案所有權**切分任務，`merge-coordinator` 在批次結束做 3-way merge（來自 Anthropic 所有權切分 + git worktree 業界實務）。徹底消滅 lost-update（修 P0-④）。
- **共享資源**：每 worktree 分配 **port 偏移**與**獨立 DB 名**，解決測試並行全綁 :8000（修 P1-⑧）。server 啟動改用 **health-check 輪詢**取代 `sleep 3`。
- **builder 介面**：採 SWE-agent ACI 精神——編輯後跑**語法守門**，拒絕任何破壞 parseable 的寫入；context 注入採 Aider repo-map（tree-sitter + PageRank 預算骨架）。

### 2.4 state-resume — action-level checkpoint
- **決策**：`run-state.json` 在每個 phase **與每個 action** 後落檔（來自 MetaGPT rc.state）；事件日誌支援確定性 replay（來自 OpenHands EventLog）。
- **exactly-once**：不可逆副作用（commit/deploy）在 resume 時不得重放——以冪等鍵 + 事件溯源保證（來自 Temporal durable execution）。單機先用 checkpoint，跨機/有副作用時升級 durable。

### 2.5 observability — JSONL gen_ai.*
- **決策**：每個 LLM/tool/檔案操作 = 一個結構化事件（`gen_ai.request.model`、`gen_ai.usage.input_tokens`、`retries`、`iteration`、`duration_ms`、`status`），採 OTel GenAI 慣例。時間戳由 shell 產生（修 P0-③）。markdown 摘要降為附屬可讀層。只追控制流與指標、不存對話內容（Anthropic 隱私姿態）。

### 2.6 safety — 沙箱 + 事後稽核
- **決策**：Codex/builder 在 OS 沙箱內執行（檔案邊界=該 worktree、網路邊界=allow-list），沙箱內**自由跑、免逐次批准**（Claude Code 模式，內部減 84% 提示）。deny-list 僅含不可逆類；其餘 allow。審查改**事後稽核 trace**取代事前批准。單一 interrupt gate 只守不可逆邊界（修 P1-⑥）。環境路徑全動態解析（修 P1-⑦）。
- **對齊使用者需求**：「不要按按鈕批准」= 沙箱 + 事後稽核，安全地達成。

### 2.7 spec-authoring — EARS + 釐清
- **決策**：需求一律 EARS 句型 + ≥1 GIVEN/WHEN/THEN scenario（OpenSpec validator 強制）。專案類型判定列入**確認 gate**（修 P1-⑪）。需求釐清採 ChatDev communicative dehallucination：不確定時**先反問再答**，不猜。

## 3. CHEAP123 落地映射

| MODE | 模型 | 範圍 |
|------|------|------|
| MODE1+2（設計+SPEC） | Opus 4.8（主腦） | 本 DESIGN/（已完成） |
| MODE3（實作） | Sonnet 4.6 並行多 agent | tasks.md 波次 |
| 跨模型審查 | fixer 與 reviewer 必不同模型 | review capability |

## 4. 風險與緩解

| 風險 | 緩解 |
|------|------|
| worktree 對非 git 專案無效 | 自動 `git init` 暫存區；或退化為「檔案所有權鎖」 |
| 跨模型審查成本翻倍 | 確定性訊號先過濾，只有過 lint/編譯的才送 LLM 審查 |
| 屬性驗證需把 EARS 編譯成斷言（工程量大） | v2.0 先做關鍵需求，其餘 v2.1 |
| 沙箱在 Windows 的支援 | Windows 用 Job Object/受限目錄；無 bubblewrap 時退化為 deny-list + 事後稽核 |

## 5. 不做什麼（避免過度工程）

- 不自建 durable-execution 引擎；單機 checkpoint 足夠，有副作用才接 Temporal/Dapr。
- 不綁定 LangGraph/AutoGen；借模式、不依賴。
- 不追 SWE-bench 分數（研究 §5：該基準已被 OpenAI 棄用、有洩漏）；以實際執行 + 屬性驗證為準。
