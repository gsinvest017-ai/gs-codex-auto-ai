# Delta for State-Resume

> action-level checkpoint、run-id 續跑、exactly-once 副作用、確定性 replay。
> 修補 REVIEW5 P1-⑨。憲章對映 C3 C6 C8。

## ADDED Requirements

### Requirement: STATE-R1 — Action-level Checkpoint
THE SYSTEM SHALL persist run state after every phase and every action to `run-state.json`, recording the in-flight action so a resumed run continues from the exact interrupted action without re-spending on completed work. [來源 MetaGPT rc.state；fixes P1-⑨]

#### Scenario: STATE-R1-S1 — 從中斷的 action 續跑
- GIVEN Phase 5 跑到 FN-007 時程序崩潰
- WHEN 以同一 run-id 重新啟動
- THEN SHALL 從 FN-007 續跑，FN-001..006 不重做、不重花 token

#### Scenario: STATE-R1-S2 — 新 run-id 為全新狀態
- GIVEN 一個全新的 run-id
- WHEN 啟動
- THEN SHALL 以空狀態開始，不載入任何舊 checkpoint

### Requirement: STATE-R2 — 確定性 Replay
THE SYSTEM SHALL store an append-only event log sufficient to deterministically replay a run for debugging. [來源 OpenHands EventLog]

#### Scenario: STATE-R2-S1 — 重建歷史
- GIVEN 一次已完成的 run 的事件日誌
- WHEN 執行 replay 工具
- THEN SHALL 依序重建每個 phase/action 的狀態轉移供檢視

### Requirement: STATE-R3 — Exactly-once 不可逆副作用
WHEN resuming a run, THE SYSTEM SHALL NOT replay irreversible side effects (commit/push/deploy) that already completed, using an idempotency key derived from the event log. [來源 Temporal；憲章 C6]

#### Scenario: STATE-R3-S1 — resume 不重複 commit
- GIVEN 一次 run 已完成某 commit 後崩潰
- WHEN 以同一 run-id resume
- THEN SHALL 偵測該 commit 的冪等鍵已存在、跳過重放，不產生重複 commit
