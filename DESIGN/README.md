# DESIGN/ — CodexAutoAI 規格庫（OpenSpec 格式）

本資料夾是 CodexAutoAI 系統的 **Single Source of Truth 規格庫**，採 **OpenSpec delta 模型**並以 **EARS** 收緊每條需求。

## 為什麼是 OpenSpec + EARS

經 [競品研究](../docs/codexAutoAI-competitive-research.md) §2.7 比較 OpenSpec / Spec-Kit / Kiro 後決定：
- **OpenSpec delta 模型**：`specs/` 是穩定 SSOT，每次變更寫成 `changes/<id>/` 內的 delta（`## ADDED/MODIFIED/REMOVED Requirements`），核可後 merge 進 `specs/`、change 移入 `archive/`。符合本專案 SSOT + Gate 治理。
- **EARS 收緊需求**：每條 `### Requirement:` 用受限句型（`WHEN/IF/WHILE/WHERE … THE SYSTEM SHALL …`），每條需求至少一個 `#### Scenario:`（GIVEN/WHEN/THEN）→ 直接可轉測試。
- **Kiro 屬性驗證**：驗收標準將編譯成對生成程式碼的自動檢查（見 `spec-authoring`）。

## 目錄結構

```
DESIGN/
├── README.md          ← 本檔（規格庫導覽）
├── project.md         ← 治理憲章（machine-readable 版的 CLAUDE.md）
├── specs/             ← 【SSOT】核可後的當前能力（archive 時由 delta 合併產生）
└── changes/
    └── 2026-06-19-v2-reliability-overhaul/   ← v2 可靠性大改的變更提案
        ├── proposal.md   ← 為什麼 + 改什麼 + 範圍（Gate 文件）
        ├── design.md     ← 技術方案（架構、被採用的競品模式、決策與取捨）
        ├── tasks.md      ← 實作 checklist（CHEAP123 波次 → MODE3）
        └── specs/        ← 【delta 規格】7 個 capability，本變更的正式 SPEC
            ├── orchestration/spec.md     ← 流水線 + 三守門終止 + escalation + 拓撲
            ├── review/spec.md            ← 跨模型 CRITIC + 屬性驗證 gate + 安全 gate
            ├── parallel-build/spec.md    ← worktree 隔離 + 所有權切分 + 動態 port
            ├── state-resume/spec.md      ← action-level checkpoint + exactly-once
            ├── observability/spec.md     ← JSONL gen_ai.* 事件 + 確定性 replay
            ├── safety/spec.md            ← OS 沙箱 + allow/deny + 事後稽核 + 單一 gate
            ├── spec-authoring/spec.md    ← EARS + 強制 scenario + 類型確認 gate
            └── security-governance/spec.md ← 信任邊界 + 供應鏈 + 密鑰遮蔽 + MODE3 授權 + 稽核完整性
```

## 閱讀順序

1. `project.md` — 不可違背的治理憲章（凌駕所有 capability）
2. `changes/.../proposal.md` — 這次改什麼、為什麼
3. `changes/.../design.md` — 怎麼改（架構與決策）
4. `changes/.../specs/*/spec.md` — 正式需求（EARS + scenario）
5. `changes/.../tasks.md` — 實作順序

## 需求編號慣例

`<CAPABILITY>-R<n>`（需求）、`<CAPABILITY>-R<n>-S<m>`（scenario）。例：`ORCH-R1`、`ORCH-R1-S1`。
每條需求標註它修補的 REVIEW5 缺口（如 `[fixes P0-①]`）以利追溯。

## 狀態

- **2026-06-19**：v2-reliability-overhaul 提案 = `draft`（待核可後 `openspec archive` 合併進 `specs/`）
- 落地方法：MODE3 並行（見 tasks.md），fixer=Codex、reviewer=Claude（跨模型，見 `review`）
