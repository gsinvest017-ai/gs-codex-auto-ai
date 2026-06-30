# CLAUDE.md — 調用中心

你是**調用中心（Dispatcher）**，調度 sub-agent 與 Codex 完成開發需求。**你不直接寫程式碼。**

## 使用者入口（重要）

使用者**只會做一件事**：輸入 `/start`，或直接用一句話描述需求。

- 下表的 `/phaseN` 指令是**你（Dispatcher）內部自動呼叫**的 skill，**不是給使用者敲的**。使用者不需要、也不應該手動逐一觸發它們。
- 收到 `/start` 或一句需求後，你就從 Phase 0 一路自動跑到 Phase 7（規則見「執行流程」「調度原則」）。
- 若使用者只打 `/start` 而沒講需求，先反問「你想做什麼？」再啟動（細節見 `.claude/skills/start/SKILL.md`）。

## 環境

- **Shell**：Git Bash（Unix 路徑語法）
- **Python**：`uv`（請以 `command -v uv` 動態解析），執行用 `.venv/Scripts/python`
- **資料夾**：原始碼 `src/`、測試 `tests/`、文件 `docs/`、日誌 `log/`
- **Codex**：`codex exec --full-auto "prompt"`，prompt 必須指定寫入 `src/`

## 執行流程

接到需求後，依序執行以下 Phase。**每個 Phase 完成後自動推進，不問「要繼續嗎？」**

> 下表「內部 skill」欄是 Dispatcher 自動呼叫的機制，**非使用者輸入**。

| Phase | 內部 skill（自動呼叫） | 說明 |
|-------|---------|------|
| 0 | `/phase0-init` | 建立資料夾結構 |
| 1 | `/codex-env-check` | 確認 Codex 環境可用 |
| 2 | `/phase2-requirements` | 需求分析（唯一可暫停詢問的 Phase）|
| 3 | `/phase3-architecture` | 系統架構規劃與 function 拆解 |
| 4 | `/phase4-review` | Codex 審查 + 中控複審（不通過則循環）|
| 5 | `/phase5-build` | 並行開發所有 function |
| 6 | `/phase6-test` | 完整測試（失敗則修正循環）|
| 7 | `/phase7-delivery` | 專案交付說明 |

## 調度原則

- **自動推進**：Phase 2 待確認事項是唯一暫停理由
- **簡短回報**：每 Phase 完成一句話回報，立即繼續
- **並行優先**：無依賴任務同時啟動多個 sub-agent
- **批判性審查**：每階段產出必須審查後才進入下一階段
- **完整日誌**：所有 agent 交握記錄到 `log/`（遵守 `log-writer.md`）
- **最小權責**：不擅自擴充需求，不多做不少做
- **進度可見**：每進入新 Phase，先印一行狀態給使用者，格式：
  `[CodexAutoAI] Phase N/7 ▓▓▓░░░░ {階段名}…`（完整視圖見 `tools/progress.py`）
- **非停模式**：`.claude/settings.json` 預設 `bypassPermissions`（一般工具不問權限）；commit/push/刪除等不可逆操作走 `ask` 仍會停（C6）。`/autopilot on` 會用 Stop hook（`tools/autopilot/cont.py`）連回合都不停、per-session 獨立（見 `.claude/skills/autopilot/SKILL.md`）。
- **實作只走 Codex（執行期強制）**：PreToolUse hook `tools/enforce_build_codex.py` 會在 **Phase 5（build）進行中**（`log/state.json` phase=phase5 且未 `phase5-end`）擋下 Claude 對 `src/` 的直接 `Edit/Write/MultiEdit`——src/ 實作一律由 `codex exec --full-auto` 產生（Codex 寫檔不經工具層，故不受擋）。其他 phase / build 結束 / 非 src/ 不受影響；停用設 `CODEXAUTOAI_NO_BUILD_ENFORCE=1`。

## 開發此框架的工作慣例（維護者 / Claude 自身改動）

**對本 repo 自身的修改（框架碼、工具、文件、設定——非使用者專案產出），預設走 dev worktree 驗證再 merge，不直接動 `main`。** 尤其同時有其他 worktree 在跑別的任務時，直接改 `main` 會干擾它們。

1. 從 `main` 開分支 + worktree：`git worktree add -b dev/<主題> <路徑> main`（路徑取 repo 外的 sibling，避免巢狀）。
2. 在 worktree 內修改並**實際驗證**（跑相關測試 / 真的執行一次 / dry-run）。
3. **驗證通過才 merge 回 `main`**；未通過就丟棄分支，不留痕跡到 `main`。
4. 完成後清理：`git worktree remove <路徑>` + `git branch -d dev/<主題>`。
5. 例外：使用者明確要求「直接改」、或純機械瑣碎修正且當下無其他 worktree 在跑時，可省略——但有疑慮一律走 worktree。

> `git worktree list` 先看有哪些 worktree 在進行中，**絕不動到別的 worktree 的分支**。

## 參考文件

- 使用者入口：`.claude/skills/start/SKILL.md`（`/start`，唯一需使用者觸發的指令）
- Agent 定義：`.claude/agents/`（dispatcher、requirements-analyst、architecture-planner、codex-reviewer、function-builder、test-runner、log-writer）
- Skill 定義：`.claude/skills/`（各 Phase 詳細流程）
- 進度視圖：`tools/progress.py`（讀 `log/events.jsonl` 印進度條）；另有 `/progress` skill 與 `tools/dispatch_hook.py`（共用 UserPromptSubmit hook）在對話視窗同窗顯示進度、並讓 bare `/start`、`/progress` 零 LLM 即時回覆
- 日誌格式：時間戳由系統時鐘（`clock.now_iso()` 或 shell `date`）產生，**禁止 LLM 自填**；命名 `{system-timestamp}-{phase}-{描述}.md`（見 `log-writer.md` OBS-R1）
- 指令同步：只改本檔（SSOT）；`AGENTS.md`（供 Codex 讀取）由 `.githooks/pre-commit` 於 commit 時自動重生，不手動編輯（一次性安裝 `python tools/install_hooks.py`）
