# Delta for Observability

> JSONL gen_ai.* 事件、shell 時戳、確定性指標、隱私姿態。
> 修補 REVIEW5 P0-③ P2-⑬。憲章對映 C3 C8。

## ADDED Requirements

### Requirement: OBS-R1 — 系統時鐘時戳
THE SYSTEM SHALL generate all timestamps from the system clock (shell `date`), and SHALL NOT accept or use any timestamp produced by an LLM. [fixes P0-③；憲章 C3]

#### Scenario: OBS-R1-S1 — 模型填的時戳被拒
- GIVEN 一個 agent 在輸出裡寫了一個時間字串
- WHEN 該輸出被記錄為事件
- THEN 事件的 `timestamp` SHALL 由 shell `date` 覆寫，忽略模型提供的值

### Requirement: OBS-R2 — 結構化 gen_ai 事件
THE SYSTEM SHALL emit one structured JSONL event per LLM call, tool call, and file operation, following OpenTelemetry GenAI conventions including model, input/output tokens, iteration, retries, duration_ms, and status. [來源 OTel GenAI；fixes P2-⑬]

#### Scenario: OBS-R2-S1 — 每次 LLM 呼叫一筆事件
- GIVEN 一次 reviewer 的 LLM 呼叫
- WHEN 呼叫完成
- THEN SHALL 寫一筆 JSONL 含 `gen_ai.request.model`、`gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens`、`duration_ms`、`status`

#### Scenario: OBS-R2-S2 — 失敗操作亦記錄
- GIVEN 一次失敗的工具呼叫
- WHEN 它擲出錯誤
- THEN SHALL 仍寫一筆 `status = error` 的事件含錯誤訊息（憲章 C8）

### Requirement: OBS-R3 — 可機器查詢的迴圈指標
THE SYSTEM SHALL record per-loop metrics (iteration count, defect-set size, cumulative cost) such that the termination guards (ORCH-R2/R3/R4) are observable after the fact.

#### Scenario: OBS-R3-S1 — 卡住的迴圈可被診斷
- GIVEN 一次因無進展而 escalate 的 run
- WHEN 事後查詢事件日誌
- THEN SHALL 能看出每次迭代的缺陷集合大小未縮小，定位卡點

### Requirement: OBS-R4 — 隱私姿態
THE SYSTEM SHALL log control flow and metrics, and SHALL NOT persist full conversation/prompt contents by default. [來源 Anthropic tracing 姿態]

#### Scenario: OBS-R4-S1 — 預設不存對話內容
- GIVEN 一次 agent 交握
- WHEN 記錄事件
- THEN SHALL 記錄控制流與指標，prompt/completion 全文預設不落盤（除非顯式開啟 debug 模式）
