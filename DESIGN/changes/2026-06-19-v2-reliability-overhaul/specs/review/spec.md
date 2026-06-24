# Delta for Review

> 跨模型 grounded 審查、屬性驗證 gate、安全 gate。
> 修補 REVIEW5 P0-② P1-⑤ P2-⑭。憲章對映 C5。

## ADDED Requirements

### Requirement: REVIEW-R1 — 跨模型審查
THE SYSTEM SHALL ensure that the model reviewing an artifact is different from the model that produced it; IF only one model is available THEN THE SYSTEM SHALL flag the review as non-independent in the event log. [fixes P0-②]

#### Scenario: REVIEW-R1-S1 — fixer 與 reviewer 不同模型
- GIVEN function-builder 使用 Codex 產出程式碼
- WHEN 進入審查
- THEN reviewer SHALL 由 Claude（或任一非 Codex 模型）執行

#### Scenario: REVIEW-R1-S2 — 單模型時誠實標註
- GIVEN 環境只有單一模型可用
- WHEN 執行審查
- THEN SHALL 在事件日誌標記 `review.independent = false`，不得佯稱獨立

### Requirement: REVIEW-R2 — Grounded CRITIC
WHEN reviewing generated code, THE SYSTEM SHALL first execute the relevant tests, compiler, and linter, and SHALL provide their actual output to the reviewer as the anchor for critique. [來源 CRITIC；審事實非意見]

#### Scenario: REVIEW-R2-S1 — 審查錨定真實訊號
- GIVEN 一份待審程式碼
- WHEN reviewer 開始審查
- THEN 其輸入 SHALL 包含實際 test/compile/lint 輸出，審查結論須引用這些訊號

#### Scenario: REVIEW-R2-S2 — 訊號失敗則直接退回
- GIVEN 編譯失敗
- WHEN 進入審查階段
- THEN SHALL 跳過 LLM 審查、直接退回修正（省成本），並記錄編譯錯誤

### Requirement: REVIEW-R3 — 屬性驗證 Gate (Phase 4.5)
THE SYSTEM SHALL compile each requirement's EARS acceptance criteria into automated assertions and run them against generated code after build and before test-delivery; IF any assertion fails THEN THE SYSTEM SHALL block progression. [來源 Kiro；fixes P1-⑤]

#### Scenario: REVIEW-R3-S1 — 程式碼違反規格被擋
- GIVEN 需求 `WHEN 輸入為空 THE SYSTEM SHALL 回傳錯誤`
- WHEN 生成程式碼對空輸入回傳 null 而非錯誤
- THEN 屬性驗證 SHALL 失敗並阻擋進入下一 phase

### Requirement: REVIEW-R4 — 安全 Gate
WHERE the project type is web/backend/auth-related, THE SYSTEM SHALL run secret-scanning and basic SAST on generated code, and IF a high-severity finding exists THEN THE SYSTEM SHALL block delivery. [fixes P2-⑭]

#### Scenario: REVIEW-R4-S1 — 硬編密鑰被擋
- GIVEN 生成的後端程式碼含硬編 API key
- WHEN 安全 gate 執行
- THEN SHALL 標記 high-severity 並阻擋交付，回報檔案與行號
