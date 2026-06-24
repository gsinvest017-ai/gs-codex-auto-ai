# Delta for Spec-Authoring

> EARS 需求、強制 scenario、類型確認 gate、釐清反問。
> 這是系統「自己對自己」強制的 SDD 紀律。
> 修補 REVIEW5 P1-⑪。憲章對映 C2 C4 C8。

## ADDED Requirements

### Requirement: AUTHOR-R1 — EARS 需求 + 強制 Scenario
THE SYSTEM SHALL express every requirement using EARS syntax (`WHEN/IF/WHILE/WHERE … THE SYSTEM SHALL …`) and SHALL reject any requirement that lacks at least one GIVEN/WHEN/THEN scenario. [來源 OpenSpec validator + Kiro EARS]

#### Scenario: AUTHOR-R1-S1 — 無 scenario 的需求被拒
- GIVEN 一條只有 SHALL 陳述、沒有任何 scenario 的需求
- WHEN 規格驗證器執行
- THEN SHALL 拒絕該需求並要求補上至少一個 scenario

#### Scenario: AUTHOR-R1-S2 — scenario 可轉測試
- GIVEN 一條 EARS 需求 + 其 GIVEN/WHEN/THEN scenario
- WHEN property-verifier（REVIEW-R3）讀取它
- THEN SHALL 能將該 scenario 編譯成自動斷言

### Requirement: AUTHOR-R2 — 專案類型確認 Gate
WHEN the requirements analyst classifies a project type, THE SYSTEM SHALL surface the classification in the Phase 2 confirmation summary, because a wrong type cascades into wrong architecture, tests, and report. [fixes P1-⑪]

#### Scenario: AUTHOR-R2-S1 — 類型誤判可被攔截
- GIVEN 分析師把一個量化回測專案誤判為 web-fullstack
- WHEN Phase 2 待確認摘要呈現
- THEN 專案類型 SHALL 明列其中供一眼確認，避免錯誤類型一路放大

### Requirement: AUTHOR-R3 — 不確定時先釐清不臆測
IF a requirement is ambiguous or underspecified, THEN THE SYSTEM SHALL ask a clarifying question before proceeding, and SHALL NOT fabricate an assumption. [來源 ChatDev communicative dehallucination；憲章 C7]

#### Scenario: AUTHOR-R3-S1 — 模糊需求觸發反問
- GIVEN 使用者需求未指明資料來源
- WHEN 分析師偵測到此模糊點
- THEN SHALL 將其列入待確認事項並反問，而非自行假設一個資料來源
