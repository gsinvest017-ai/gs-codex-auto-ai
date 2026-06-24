# Proposal: v2 Reliability Overhaul

> Change ID: `2026-06-19-v2-reliability-overhaul` · Status: `draft` · Author: System Architect
> 依據：[REVIEW5](../../../docs/codexAutoAI-review5.md) + [競品研究](../../../docs/codexAutoAI-competitive-research.md)

## Why（為什麼）

CodexAutoAI 的架構骨架（大腦/寫手分離、契約驅動流水線、類型自適應）已是 MetaGPT 等級的設計，但 REVIEW5 找出 **4 個 P0 + 7 個 P1** 全集中在「運行時可靠性」——這正是它落後整個 autonomous-dev 賽道一個世代之處。Devin 在真實任務 ~85% 非成功、且會「追不可能的任務超過一天」，印證**沒有可靠的終止/審查/隔離機制，自主性本身就是負債**。

四個 P0 若不補，這套系統在任何真實長流程都會跑壞：
1. 循環無界 → 無限燒 token
2. 審查非獨立 → 同模型盲點放行錯誤
3. 時間戳造假 → SSOT 根基不可信
4. 並行寫檔競爭 → 程式碼互相覆蓋

## What Changes（改什麼）

引入 7 個新 capability，把競品已驗證的解法落地（見各 `specs/*/spec.md`）：

| Capability | 修補 | 核心機制（來源） |
|------------|------|------------------|
| **orchestration** | P0-①④⑩ | 三守門終止（max-iter + no-progress + budget）→ escalation；拓撲排序；確定性外殼（LangGraph/Magentic-One/CrewAI Flows） |
| **review** | P0-② P1-⑤⑭ | 跨模型 CRITIC（錨定測試/編譯）+ 屬性驗證 gate + 安全 gate（CRITIC/CriticGPT/Kiro） |
| **parallel-build** | P0-④ P1-⑧ | worktree-per-agent + 所有權切分 + coordinator 合併 + 動態 port/db（Anthropic/git worktree/SWE-agent ACI） |
| **state-resume** | P1-⑨ | action-level checkpoint + run-id 續跑 + exactly-once 副作用（MetaGPT rc.state/OpenHands/Temporal） |
| **observability** | P0-③ P2-⑬ | shell 時戳 + JSONL `gen_ai.*` 事件 + 確定性 replay（OTel GenAI/Anthropic tracing） |
| **safety** | P1-⑥⑦ | OS 沙箱 + allow/deny + 事後稽核 + 單一 interrupt gate + 動態路徑（Claude Code/OpenHands） |
| **spec-authoring** | P1-⑪ | EARS + 強制 scenario + 類型確認 gate + 釐清反問（OpenSpec/Kiro/ChatDev dehallucination） |
| **security-governance** | SEC-1..5 GOV-6..9 | 信任邊界（指令-資料分離）+ 供應鏈釘選（防 slopsquatting）+ 密鑰遮蔽 + 不受信程式碼沙箱 + 框架完整性 + MODE3 帶外授權 + 稽核 hash chain（MetaGPT CVE 類 / Claude Code 信任模型） |

## Scope（範圍）

**In scope**：上述 7 capability 的 SPEC（本變更）；MODE3 實作（見 tasks.md）。
**Out of scope**：分支產物（obsidian-ops/遊戲/股票/X1）；更換底層 LLM provider；UI dashboard（v2.1 再議）。
**Breaking changes**：`log/` 散文格式 → JSONL（保留 markdown 摘要為附屬）；Phase 5 由「同 src/ 並行」改為「worktree 並行」；新增 Phase 4.5（code review）與 escalation 終端狀態。

## Impact（影響）

- **agent 定義**：`dispatcher` / `codex-reviewer` / `function-builder` / `test-runner` / `log-writer` 需依新 capability 改寫。
- **新增**：`escalation-handler`、`merge-coordinator`、`property-verifier` 三個 agent 角色。
- **相容性**：v1 流水線的 7-phase 編號保留，行為向後相容（既有 phase skill 仍可用，只是被新 Gate 包住）。

## 差異化目標

研究 §4 指出**沒有任何競品同時做齊**：真並行防碰撞、真跨模型審查、durable resume、server-native HITL 最小化。v2 做齊這四項即**規格上超越現有產品級**。

## 核可標準（Gate）

本提案核可需滿足：每個 capability 的每條需求都有 ≥1 EARS scenario；`tasks.md` 每個 task 對映至少一條需求；與 `project.md` 憲章無衝突。
