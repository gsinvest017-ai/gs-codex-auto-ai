# CodexAutoAI — 競品研究與優化方向

> 研究日期：2026-06-19 · 範圍：系統框架本身 · 方法：6 路並行研究 agent（CHEAP123 並行精神）
> 配套文件：[REVIEW5 審查](codexAutoAI-review5.md)（缺口來源）、[v2 SPEC](../DESIGN/README.md)（解法落地）

---

## 0. 一句話結論

> 把 CodexAutoAI 跟 9 個競品逐項比較後，**它的「契約驅動流水線」骨架其實已是 MetaGPT 等級的設計，但「運行時可靠性」落後整個賽道一個世代**。所有 REVIEW5 的 P0/P1 缺口，競品都有「已驗證、可直接移植」的解法。本研究把這些解法對齊到我們的缺口，作為 v2 SPEC 的依據。

---

## 1. 競品全景

| 系統 | 類別 | 與本系統的關係 |
|------|------|----------------|
| **MetaGPT** | SOP 多 Agent 軟體公司 | 最像我們的對手——`Code = SOP(Team)`，PRD→Design→Tasks→Code 契約鏈 |
| **ChatDev** | Chat 多 Agent 公司 | 雙人對話相位鏈 + 獨立 Reviewer/Tester 角色 |
| **AutoGen** (MS) | 多 Agent 編排框架 | actor 模型 + 可組合終止條件 + Magentic-One 雙帳本 |
| **CrewAI** | 多 Agent 編排框架 | Crews（自治）vs Flows（確定性）雙層 |
| **OpenHands** | 自主 SWE agent | 事件溯源 state + StuckDetector + 安全分析器 |
| **Devin** | 自主 SWE agent | coordinator + N 個隔離 VM 子 Devin |
| **SWE-agent** | 自主 SWE agent | ACI（Agent-Computer Interface）+ 編輯語法守門 |
| **Aider / Cline / GPT-Pilot** | 程式碼生成/結對 | repo map、模型專屬 diff 格式、git-per-change |
| **OpenSpec / Spec-Kit / Kiro** | 規格驅動開發 (SDD) | 我們要採用的規格方法論 |

---

## 2. 七大可靠性維度——競品怎麼解，我們缺什麼

### 2.1 循環終止（對應 REVIEW5 P0-①）

| 系統 | 機制 |
|------|------|
| MetaGPT | `n_round` + `$investment`→`CostManager` 預算上限 + **debug 迴圈硬上限 3 次** |
| ChatDev | `cycleNum` 上限 + **「連續兩次修改無變化」收斂停止** |
| AutoGen | **可組合終止代數**：`MaxMessageTermination` `\|` `TokenUsageTermination` `\|` `TimeoutTermination` |
| Magentic-One | **stall counter**：~2 次無進展 → 自省 → 重新規劃 |
| LangGraph | `recursion_limit`（預設 25）作為**安全網而非終止邏輯**——官方明言「上限只是讓你燒 1000 次而非 25 次 API」 |

**結論**：終止 = 三道獨立守門，全部缺一不可：
1. **max-iteration 硬上限**（確定性天花板）
2. **no-progress 偵測**（critic 回報的缺陷集合 hash 比對，連續 N 次不縮小 = 卡住）← **這是我們唯一最缺、也最關鍵的一道**
3. **budget ceiling**（token/$/wall-clock）
→ 三者任一觸發都路由到**單一 escalation 終端節點**，而非死循環。

### 2.2 獨立審查（對應 REVIEW5 P0-②）

| 系統 | 審查獨立性 |
|------|-----------|
| MetaGPT | 同一 Engineer 換 prompt 自審 → **弱（盲點相關）** |
| ChatDev | **獨立 Reviewer/Tester 角色**，但同一個模型 |
| 學界 | **CRITIC**：審查錨定在**真實外部訊號**（測試/編譯/lint 輸出），非模型意見 |
| 跨模型/對抗 | 不同模型互審 + 投票，實測降低幻覺；Anthropic 用獨立 CitationAgent |

**結論**：分層 critic：
1. **確定性訊號優先**——先跑測試/編譯/lint，把**結果**餵給審查者（CRITIC 錨點）
2. **不同模型**做推理審查（fixer 與 reviewer 共用權重 = 共用盲點，異質性正是重點）
3. 投票/ensemble 只在高風險 merge 時用（成本）
→ 對映到我們：**Codex 寫、Claude 審**（或反之），且審查必須先有測試/編譯結果。

### 2.3 並行不衝突（對應 REVIEW5 P0-④）

| 系統 | 機制 |
|------|------|
| Anthropic 研究系統 | **所有權切分**：每個 subagent 自含任務、自己的 context、**不知道彼此存在**；coordinator 合併**引用**而非共享檔案寫入 |
| 業界實務 | **git worktree-per-agent**：各自獨立 HEAD/index/working tree，消滅 lost-update 與 `index.lock` 爭用；衝突延後到 merge 時用 3-way merge 偵測 |
| 注意 | 共享非檔案資源仍會撞——需 **per-worktree port 偏移 + 獨立 DB 名** |

**結論**：worktree 每 agent 一份（物理隔離）＋ 按檔案/模組切所有權（邏輯隔離）＋ coordinator 合併。我們現在按「呼叫依賴」分批，沒解「同檔寫入衝突」——這是直接的差異化機會（MetaGPT/ChatDev 兩家都是**純序列**，連並行都沒有）。

### 2.4 狀態持久化 / Resume（對應 REVIEW5 P1-⑨）

| 系統 | 機制 |
|------|------|
| MetaGPT | `team.json` 序列化 + **action-level `rc.state`**：從中斷的「那個 action」續跑，不重花 token |
| OpenHands | 事件溯源（immutable EventLog + 單一 ConversationState）→ **確定性 replay**、time-travel debug |
| LangGraph | checkpointer 每 super-step 存檔，`thread_id` 當游標續跑 |
| Temporal（durable execution）| checkpoint 不等於 durable——durable 多了**自動失敗偵測、自動重試、exactly-once side effects**（resumed pipeline 不會重複 commit/deploy） |

**結論**：先做 checkpoint（每 phase/action 落 `state.json`，run-id 續跑）；當有**非冪等副作用**（commit/deploy）或跨機 fan-out 時，升級到 durable execution。對我們的 review-fix 迴圈，「apply fix / commit」這步的 exactly-once 是決定性因素。

### 2.5 可觀測性（對應 REVIEW5 P2-⑬）

**標準浮現**：OpenTelemetry **GenAI 語意慣例**（`gen_ai.*` 命名空間）——統一 model、token（prompt/completion）、finish reason、latency、cost。每個 LLM 呼叫 / tool 呼叫 / 檢索 = 一個 child span → 完整推理鏈 trace。成本與 token 相關（非請求數）→ 可近即時算花費。Anthropic：「加上完整 production tracing 後才能系統性診斷 agent 為何失敗」，且只追**控制流與指標、不追對話內容**（隱私）。

**結論**：我們的 markdown 散文日誌升級為 **JSONL 結構化事件**（phase, status, retries, duration, tokens），用 `gen_ai.*` 慣例。最便宜的高槓桿改動——「看不見的迴圈無法修」。

### 2.6 沙箱與權限（對應 REVIEW5 P1-⑥）

| 系統 | 機制 |
|------|------|
| OpenHands | 每 session Docker 隔離 + **安全分析器**（LOW/MED/HIGH 風險）+ 確認策略「confirm, don't block」 |
| Claude Code | **OS 級沙箱**（Linux bubblewrap / macOS Seatbelt）強制檔案+網路邊界；因 OS 強制邊界，**沙箱內可自由跑、免逐次批准**（內部減少 84% 權限提示，廠商數據） |
| 原則 | allow/ask/deny + 模式：routine 自動跑、只在**不可逆類**（覆寫/刪除/deploy）提示。逐指令批准會造成「approval fatigue」橡皮圖章——**少而高訊號的提示反而更安全** |

**結論**：沙箱 + allow/deny + **事後稽核取代事前批准**，只在不可逆邊界留**單一** interrupt gate。直接呼應使用者「不要按按鈕批准」需求——但安全地實現。

### 2.7 規格嚴謹度 / SDD（對應 REVIEW5 P1-⑪，且為本任務交付格式）

**三家 SDD 工具的共識**——讓規格「可執行 / agent-ready」的要件：
1. **受限自然語言**寫需求：Kiro 的 **EARS**（`WHEN/IF/WHILE/WHERE … THE SYSTEM SHALL …`）+ OpenSpec 的 SHALL/MUST + GIVEN/WHEN/THEN scenario → 每條需求**直接可轉成測試**
2. **每條需求必須有 ≥1 驗收 scenario**（OpenSpec validator 強制）——無孤兒需求
3. **what/why（spec）→ how（design）→ do（tasks）三段分離**，tasks 是 checkbox 可機器追蹤
4. **constitution/治理層**凌駕功能（Spec-Kit `constitution.md` / Kiro `.kiro/steering/`）——我們的 CLAUDE.md 正是
5. **相位間 approval gate**；OpenSpec 獨有 **archive/merge gate**（delta 合進 SSOT）
6. **可追溯**：編號需求 ↔ 編號 task ↔（Kiro）屬性驗證連結 EARS 到程式碼檢查
7. **規格是 agent 的執行期資源**，不是給人看的文件——agent 在執行期**查詢**規格並據以驗證程式碼

**採用決策**：以 **OpenSpec 的 delta 模型**為撰寫格式（`## ADDED/MODIFIED/REMOVED Requirements`，SSOT spec 穩定、每次變更是可審 diff，archive 時合併），**並在 scenario 內用 EARS 收緊**每條需求（EARS 需求 + GIVEN/WHEN/THEN scenario 是三家最強組合），再吸收 **Kiro 屬性驗證**（把驗收標準編譯成對生成程式碼的自動檢查，閉合「規格說 X、程式碼做 Y」的洞）。

---

## 3. 「值得偷」精選清單（→ v2 SPEC）

| # | 來源 | 點子 | 落地於 v2 capability |
|---|------|------|----------------------|
| 1 | LangGraph + ChatDev | no-progress（缺陷集合不縮小）+ max-iter + budget 三守門 → escalation | `orchestration` |
| 2 | CRITIC + 跨模型 | 審查錨定測試/編譯輸出 + 不同模型審查 | `review` |
| 3 | Anthropic + git | worktree-per-agent + 所有權切分 + coordinator 合併引用 | `parallel-build` |
| 4 | MetaGPT + OpenHands | action-level checkpoint + 事件溯源 replay | `state-resume` |
| 5 | OTel GenAI | `gen_ai.*` span-per-step + JSONL 事件 | `observability` |
| 6 | Claude Code + OpenHands | OS 沙箱 + allow/deny + 事後稽核 + 單一 interrupt gate | `safety` |
| 7 | OpenSpec + Kiro | EARS 需求 + 強制 scenario + 屬性驗證 | `spec-authoring` |
| 8 | SWE-agent | ACI + 編輯語法守門（拒絕破壞 parseable 的編輯） | `parallel-build`（builder 介面） |
| 9 | Aider | repo map（tree-sitter + PageRank）給模型有預算的程式碼骨架 | `parallel-build`（context） |
| 10 | Magentic-One | stall counter → 自省 → 重新規劃 | `orchestration`（escalation 前的最後一搏） |
| 11 | ChatDev | communicative dehallucination（不確定時先反問再答） | `spec-authoring`（需求釐清） |
| 12 | CrewAI | Crews/Flows 分離——把 LLM 不確定性關進確定性 graph | `orchestration`（確定性外殼） |

---

## 4. 競品的共同盲點 = 我們的差異化機會

研究橫切下來，**沒有任何一家同時做到**這四件事：
1. **真正的並行模組建置 + 防碰撞寫入**（MetaGPT/ChatDev 純序列；OpenHands 委派是阻塞式）
2. **真正獨立（跨模型）的審查**（多數是同模型自審）
3. **durable execution 級的 resume**（多數只有手動 checkpoint）
4. **server-native 的 HITL 最小化**（CrewAI 是 stdin-bound 的痛點）

→ **CodexAutoAI v2 若把這四項做齊，規格上即超越現有產品級。**

---

## 5. 現實校準（避免過度樂觀）

- **基準有水分**：SWE-bench Verified 有 ~32% 解答洩漏、~94% 實例早於模型 cutoff，OpenAI 已於 2026 初**棄用** SWE-bench Verified（飽和 + 測試瑕疵）。→ 我們的 Phase 6 不能只信 pass rate，要保留**實際執行 + 屬性驗證**。
- **Devin 真實表現**：Answer.AI 實測 20 任務 3 成功 / 14 失敗，且**會追不可能的任務超過一天**——印證「自主性本身就是負債，除非有早期『我卡住了，原因是…』的升級機制」。→ 我們的 escalation 節點是必需品，不是 nice-to-have。
- **AutoGen 策略漂移**：正併入 Microsoft Agent Framework，0.4 僅修 bug。→ 借鑑其模式，但**不綁定**任何單一框架。

---

*研究報告完。下一步：[DESIGN/](../DESIGN/README.md) 內的 OpenSpec 格式 SPEC 把以上落地為可實作需求。*
