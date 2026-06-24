# Tasks: v2 Reliability Overhaul

> 實作 checklist。每個 task 對映 ≥1 需求。波次依賴拓撲排序。
> 落地法：MODE3 並行（Sonnet 多 agent），fixer=Codex / reviewer=Claude（跨模型）。
> checkbox 為機器可追蹤完成狀態（OpenSpec 慣例）。

## ✅ 已完成 — v2.0-core 引擎（2026-06-19，MODE3 CHEAP123）

確定性編排引擎 + 12 capability 模組已實作於 `src/codexautoai_v2/`，**278 tests 全綠**（266 模組單元 + 12 端到端整合）。落地法：Opus 定契約+整合，10× Sonnet agent 並行（按檔案所有權切分，零碰撞）。

| 模組 | 對應需求 | 狀態 |
|------|----------|------|
| clock.py / events.py | OBS-R1/R2/R3/R4, SECGOV-R3 | ✅ shell 時戳 + JSONL gen_ai.* + 密鑰遮蔽 |
| termination.py | ORCH-R2/R3/R4 | ✅ 三守門（max-iter + no-progress + budget）|
| escalation.py | ORCH-R5 | ✅ 單次 replan → 終端 escalation |
| depgraph.py | ORCH-R6 | ✅ 拓撲排序 + 環偵測 |
| state.py | STATE-R1/R2/R3, SECGOV-R8 | ✅ action-level checkpoint + exactly-once + schema 驗證 |
| audit.py | SECGOV-R8 | ✅ hash-chain 防竄改稽核 |
| ownership.py | BUILD-R2 | ✅ 檔案所有權切分 |
| safety.py | SAFE-R2/R4, SECGOV-R5/R6/R7 | ✅ allow/deny + 框架完整性 + MODE3 帶外授權 + 動態路徑 |
| supplychain.py | SECGOV-R2 | ✅ 釘選 + 幻覺套件阻擋 |
| spec_authoring.py | AUTHOR-R1/R2 | ✅ EARS 驗證 + 類型確認 |
| review.py | REVIEW-R1/R2 | ✅ 跨模型選擇 + grounded CRITIC |
| orchestrator.py | ORCH-R1 | ✅ 確定性控制流外殼，整合上述全部 |

## ✅ 已完成 — v2.1（2026-06-19，MODE3 最大 fan-out：8 新模組 + 7 agent 改寫並行）

新增 8 模組（+295 tests）+ 改寫 7 個既有 agent 定義對齊 v2 + 整合進 orchestrator。**全套 578 tests 全綠**。

| 新模組 | 需求 | 狀態 |
|--------|------|------|
| worktree.py | BUILD-R1 | ✅ git worktree per builder（+ git init fallback）|
| merge_coordinator.py | BUILD-R3 | ✅ 3-way merge + 衝突回報（不覆蓋）|
| portman.py | BUILD-R4 | ✅ 動態 free-port + 獨立 DB 名 + health-check 輪詢（取代 sleep 3）|
| syntax_guard.py | BUILD-R5 | ✅ ast/json 解析守門，拒絕破壞 parseable 的編輯 |
| property_verifier.py | REVIEW-R3 | ✅ EARS scenario → 可執行斷言 + 驗證報告 |
| secret_scan.py | REVIEW-R4 | ✅ secret 掃描 + SAST，HIGH 阻擋交付 |
| injection_guard.py | SECGOV-R1 | ✅ prompt-injection 偵測 + 指令/資料分離 |
| repo_map.py | BUILD context | ✅ ast + ref-rank 的預算化程式碼骨架（Aider 式）|

agent 改寫：dispatcher / codex-reviewer / function-builder / test-runner / log-writer / requirements-analyst / architecture-planner 全部對齊 v2（引用 requirement IDs + 引擎模組）。orchestrator.py 新增 `intake_requirement` / `guard_builder_write` / `security_gate` / `property_gate` / `allocate_test_resources` / `build_with_worktrees` / `context_map`，wire 入全部新模組。

**尚未做（需 live Codex 流水線端到端，列為 v2.2）**：跑一個真實小專案完整走完 7-phase v2（4.3）；OS 沙箱實掛（SAFE-R1，目前是 policy 層，Windows 退化策略待接 Job Object）；fa5 全系統稽核（待 session limit 9am 重置後跑）。

---


## 波次 0 — 基礎設施（無依賴，最先）

- [ ] 0.1 建立確定性編排外殼 `orchestrator/`（狀態機 + phase 推進 + Gate hook）→ ORCH-R1
- [ ] 0.2 `run-state.json` schema + 讀寫器（action-level checkpoint）→ STATE-R1
- [ ] 0.3 JSONL 事件匯流排 + `gen_ai.*` event schema + shell 時戳產生器 → OBS-R1, OBS-R2
- [ ] 0.4 環境動態解析器（uv/python/port 探測，移除硬編路徑）→ SAFE-R4

## 波次 1 — 終止與審查核心（依賴波次 0）

- [ ] 1.1 三守門終止：max_iterations 計數器 → ORCH-R2
- [ ] 1.2 三守門終止：no-progress 缺陷集合 hash 比對 → ORCH-R3
- [ ] 1.3 三守門終止：budget ceiling（token/$/wall-clock）→ ORCH-R4
- [ ] 1.4 `escalation-handler` agent（攜帶 diff + 未解 critique 升級）→ ORCH-R5
- [ ] 1.5 FN 依賴圖拓撲排序 + 環偵測 → ORCH-R6
- [ ] 1.6 跨模型 reviewer：強制 fixer≠reviewer 模型 → REVIEW-R1
- [ ] 1.7 grounded CRITIC：先跑測試/編譯/lint，結果餵 reviewer → REVIEW-R2

## 波次 2 — 並行與驗證（依賴波次 1）

- [ ] 2.1 worktree-per-builder 配置（自動 git init fallback）→ BUILD-R1
- [ ] 2.2 檔案所有權切分器（同檔 FN 序列化）→ BUILD-R2
- [ ] 2.3 `merge-coordinator`（批次結束 3-way merge）→ BUILD-R3
- [ ] 2.4 per-worktree port 偏移 + 獨立 DB 名 + health-check 輪詢 → BUILD-R4
- [ ] 2.5 builder 編輯語法守門（拒絕破壞 parseable 的寫入）→ BUILD-R5
- [ ] 2.6 `property-verifier`：EARS 驗收標準 → 程式碼斷言（Phase 4.5）→ REVIEW-R3
- [ ] 2.7 安全 gate：secret 掃描 + 基本 SAST → REVIEW-R4

## 波次 3 — 安全外殼與規格紀律（依賴波次 2）

- [ ] 3.1 OS 沙箱包裝（檔案+網路邊界；Windows 退化策略）→ SAFE-R1
- [ ] 3.2 allow/deny 規則 + 單一 interrupt gate（不可逆邊界）→ SAFE-R2, SAFE-R3
- [ ] 3.3 事後稽核 trace 檢視器 → SAFE-R3, OBS-R3
- [ ] 3.4 EARS 需求驗證器（每需求 ≥1 scenario，否則拒絕）→ AUTHOR-R1
- [ ] 3.5 專案類型確認 gate + 釐清反問機制 → AUTHOR-R2, AUTHOR-R3
- [ ] 3.6 確定性 replay 工具（從事件日誌重建）→ STATE-R2, OBS-R4
- [ ] 3.7 指令-資料分離 / prompt-injection 防禦（不受信內容不當指令執行）→ SECGOV-R1
- [ ] 3.8 依賴供應鏈控制（lockfile + hash 釘選 + 幻覺套件阻擋）→ SECGOV-R2
- [ ] 3.9 密鑰遮蔽器（事件/報告/交握全鏈 redact）→ SECGOV-R3
- [ ] 3.10 框架完整性邊界（禁寫 .claude/CLAUDE.md/DESIGN）+ MODE3 帶外授權閘 → SECGOV-R5, SECGOV-R6
- [ ] 3.11 稽核 hash chain + checkpoint schema 驗證（防竄改/不安全反序列化）→ SECGOV-R8
- [ ] 3.12 harness 權限層 deny 規則（commit/push/history-rewrite）✅ 全域 settings.json 已落地 → SECGOV-R7

## 波次 4 — 整合與遷移（依賴全部）

- [ ] 4.1 改寫 5 個既有 agent（dispatcher/reviewer/builder/test-runner/log-writer）對齊新 capability
- [ ] 4.2 清理 `SKILL.md`/`skill.md` 雙份，留一份 → REVIEW5 P2-⑫
- [ ] 4.3 端到端回歸：用一個已知小專案跑完整 v2 流水線，驗證 escalation/resume/replay
- [ ] 4.4 `openspec archive` 把本 delta 合併進 `DESIGN/specs/`

## 完成定義 (Definition of Done)

- 每條需求的 scenario 都有對映的自動測試且通過
- 4 個 P0 缺口各有回歸測試證明已修
- 端到端跑通：含一次刻意觸發的 escalation + 一次中途斷線 resume + 一次 replay
- 與 `project.md` 憲章零衝突（C1–C9 全數遵守）
