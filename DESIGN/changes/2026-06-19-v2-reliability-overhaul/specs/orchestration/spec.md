# Delta for Orchestration

> 確定性編排外殼：phase 推進、三守門終止、escalation、拓撲排序。
> 修補 REVIEW5 P0-① P0-④(分批) P1-⑩。憲章對映 C2 C5。

## ADDED Requirements

### Requirement: ORCH-R1 — 確定性控制流
The system SHALL drive all phase transitions, loops, and branching from deterministic orchestrator code, and SHALL NOT delegate control-flow decisions to an LLM.

#### Scenario: ORCH-R1-S1 — phase 推進由程式決定
- GIVEN 一個 phase 的產出已通過其 Gate
- WHEN orchestrator 評估下一步
- THEN 由狀態機（非 LLM）決定進入下一 phase，並寫入一筆 phase-transition 事件

#### Scenario: ORCH-R1-S2 — LLM 不得自選控制流
- GIVEN 一個 agent 節點回傳了輸出
- WHEN 輸出中包含「跳過某 phase」之類的控制指令
- THEN orchestrator SHALL 忽略該指令，僅採用其資料內容

### Requirement: ORCH-R2 — 最大迭代上限
WHEN any review-fix or test-fix loop executes, THE SYSTEM SHALL enforce a configurable `max_iterations` (default 3) and route to escalation when exceeded. [fixes P0-①]

#### Scenario: ORCH-R2-S1 — 達上限即升級
- GIVEN `max_iterations = 3` 的測試修正迴圈
- WHEN 第 3 次修正後測試仍失敗
- THEN orchestrator SHALL 停止迴圈並觸發 ORCH-R5 escalation，不得進行第 4 次

### Requirement: ORCH-R3 — 無進展偵測
WHILE a fix loop is running, THE SYSTEM SHALL compute a normalized hash of the reviewer/tester defect set each iteration, and IF the defect set fails to shrink for 2 consecutive iterations THEN THE SYSTEM SHALL treat the loop as stuck and route to escalation. [fixes P0-① 最關鍵守門]

#### Scenario: ORCH-R3-S1 — 缺陷集合不縮小即判定卡住
- GIVEN 第 N 次迭代回報缺陷集合 {A,B}
- WHEN 第 N+1、N+2 次迭代回報的缺陷集合 hash 與前次相同或未縮小
- THEN orchestrator SHALL 判定卡住並 escalate，即使尚未達 max_iterations

#### Scenario: ORCH-R3-S2 — 有進展則重置計數
- GIVEN 連續無進展計數為 1
- WHEN 下一次迭代缺陷集合縮小（如 {A,B}→{A}）
- THEN 無進展計數 SHALL 重置為 0

### Requirement: ORCH-R4 — 預算上限
THE SYSTEM SHALL track cumulative tokens, cost, and wall-clock per run, and WHEN any configured ceiling is reached THE SYSTEM SHALL halt further agent calls and route to escalation. [fixes P0-①]

#### Scenario: ORCH-R4-S1 — 超預算即停
- GIVEN token 上限 500k
- WHEN 累計 token 達 500k
- THEN orchestrator SHALL 停止派遣新 agent 並 escalate，回報已用量

### Requirement: ORCH-R5 — Escalation 終端
WHEN any termination guard (ORCH-R2/R3/R4) trips, THE SYSTEM SHALL first attempt exactly one replan, and IF the replan does not resolve the blocker THEN THE SYSTEM SHALL enter a terminal escalation state carrying the current diff and unresolved critique. [來源 Magentic-One stall→replan；修 Devin「追不可能任務」類]

#### Scenario: ORCH-R5-S1 — 升級而非死循環
- GIVEN 一個觸發了無進展守門的迴圈
- WHEN 單次 replan 後問題仍在
- THEN 進入 escalation 終端狀態、記錄完整脈絡，**絕不**回到原迴圈無限重試

### Requirement: ORCH-R6 — 依賴拓撲排序與環偵測
WHEN planning parallel build batches, THE SYSTEM SHALL topologically sort the FN dependency graph, and IF a cycle is detected THEN THE SYSTEM SHALL reject the architecture with the offending cycle reported. [fixes P1-⑩]

#### Scenario: ORCH-R6-S1 — 循環依賴被擋
- GIVEN FN-A 依賴 FN-B 且 FN-B 依賴 FN-A
- WHEN orchestrator 規劃分批
- THEN SHALL 報錯指出環 `A→B→A`，不進入 Phase 5
