# CodexAutoAI — 專案架構分析

> 分析日期：2026-06-19
> 分析範圍：`<project-root>/codexAutoAI-master`
> 一句話定位：**Claude 當「大腦／調用中心」，OpenAI Codex 當「寫手」的多 Agent 全自動開發系統**，外加它歷次跑出來的產物。

---

## 0. TL;DR（先看這段）

這個 repo 其實是**兩層東西疊在同一個資料夾裡**，看的時候要分開理解：

| 層 | 是什麼 | 代表檔案／資料夾 |
|----|--------|------------------|
| **A. 框架層（核心）** | CodexAutoAI 本體：一套「Claude 調度、Codex 寫碼」的自動開發流水線 | `CLAUDE.md`、`README.md`、`.claude/agents/`、`.claude/skills/`、`init.md`、`ops.py`(launcher 樣板) |
| **B. 產物層（輸出）** | 框架歷次跑出來的實際專案，全堆在 `src/`、`contracts/`、`docs/`、`vault/`，以及多個獨立子資料夾 | `src/`、`contracts/X1.*`、`obsidian-ops-dashboard/`、`stockpilot/`、`COSTKILLER/`、`TOKENGUARD/`、`DQN/DNQ/RAINBOWSIX` 等 |

**重點觀察**：框架層是這個專案真正的「架構」；產物層是「成果倉庫」，且 `src/` 內**多個不相關的專案混居**（Obsidian 知識庫 + X1 契約整合 + Roguelike 遊戲 + 股票儀表板），這是反覆用同一個 `src/` 跑不同需求留下的結果。

---

## 1. 框架層架構（核心）

### 1.1 角色分工

```
┌─────────────────────────────────────────────────────────────┐
│  使用者需求                                                    │
└───────────────┬─────────────────────────────────────────────┘
                ▼
        ┌───────────────┐
        │  Claude Code  │  = 大腦 / 調用中心 (Dispatcher)
        │  「不直接寫碼」 │     調度、審查、記錄、推進
        └───────┬───────┘
                │ 派遣 sub-agent / 下 prompt
       ┌────────┼─────────────────────────┐
       ▼        ▼                          ▼
  sub-agents  ┌──────────────────┐   log/ (SSOT 日誌)
  (7 種)      │  OpenAI Codex CLI │
              │  codex exec       │  = 寫手
              │  --full-auto      │     實際產生 src/ 程式碼
              └──────────────────┘
```

- **Claude = Dispatcher（調用中心）**：`CLAUDE.md` 明文「**你不直接寫程式碼**」。負責接需求、拆 phase、派 agent、批判性複審、推進、寫 log。
- **Codex = 寫手**：透過 `codex exec --full-auto "prompt"` 實際把程式碼寫進 `src/`。
- **Sub-agents**：Claude 底下 7 個專責 agent（見 1.3）。

### 1.2 七階段流水線（7-Phase Pipeline）

定義於 `CLAUDE.md` 與 `.claude/skills/phase*`。核心原則：**每個 Phase 完成後自動推進，不問「要繼續嗎？」**，唯一允許暫停的只有 Phase 2（需求待確認）。

| Phase | Skill | 動作 | 產出 |
|-------|-------|------|------|
| **0** | `phase0-init` | 建立 `src/ tests/ docs/ log/` 標準結構 | 資料夾骨架 |
| **1** | `codex-env-check` | 確認 Codex CLI 可用（讀 `init.md`） | 環境驗證 |
| **2** | `phase2-requirements` | 派 `requirements-analyst`：判斷專案類型、拆功能、列待確認 | `docs/requirements-spec.md` |
| **3** | `phase3-architecture` | 派 `architecture-planner`：拆 function(FN-xxx)、定介面、分析依賴 | `docs/architecture.md` |
| **4** | `phase4-review` | 派 `codex-reviewer` 用 Codex 審查 + Dispatcher 批判性複審（不過則循環） | 審查報告 |
| **5** | `phase5-build` | 依賴分批，**並行**派多個 `function-builder`（各自呼叫 Codex 寫碼） | `src/` 程式碼 |
| **6** | `phase6-test` | 先建環境 → 依專案類型並行派 `test-runner` 實際執行測試（失敗則修正循環） | 測試報告 |
| **7** | `phase7-delivery` | 建 `.venv`、裝依賴、填真實數據產出類型專屬報告 + 交付說明 | `docs/{專案}-report.md` |

#### 控制流（含循環點）

```
Phase 0 → 1 → 2 ──(有待確認?)──暫停問使用者──┐
                                              │
              └──(無待確認)──────────────────┘
                          ▼
              3 → 4 ──(複審不過)──→ 修正架構 ─┐
                   │                          │
                   └──(通過)─────◄────────────┘
                          ▼
              5（並行 build）→ 6 ──(測試失敗)──→ 派 builder 修正 ─┐
                                  │                              │
                                  └──(全通過)──◄─────────────────┘
                                         ▼
                                   7（交付）→ 結束
```

### 1.3 Sub-agents（`.claude/agents/`）

| Agent | 角色 | 對應 Phase |
|-------|------|-----------|
| `dispatcher.md` | 調用中心本體，定義整套推進邏輯 | 全程 |
| `requirements-analyst.md` | 需求分析、判斷專案類型、命名專案 | 2 |
| `architecture-planner.md` | 系統架構、function 拆解、依賴分析、測試策略 | 3 |
| `codex-reviewer.md` | 呼叫 Codex 審查架構 vs 需求契合度 | 4 |
| `function-builder.md` | 呼叫 Codex 實作單一 FN，強制寫入 `src/` | 5 |
| `test-runner.md` | 實際執行測試（非僅 import 驗證），依類型切策略 | 6 |
| `log-writer.md` | 日誌規範：`{YYYYMMDD-HHmmss}-{phase}-{描述}.md` | 全程 |

**專案類型分流**：`requirements-analyst` 會判定 `web-fullstack / web-backend / data-science / quant-finance / ml-ai / cli-tool / library / desktop-gui / automation`，後續 Phase 3/6/7 依類型切換架構模式、測試策略與報告模板。

### 1.4 Skills（`.claude/skills/`）

- 流程型：`phase0-init`、`phase2-requirements`、`phase3-architecture`、`phase4-review`、`phase5-build`、`phase6-test`、`phase7-delivery`
- 工具型：`codex-env-check`（環境檢查）、`codex-run`（直接呼叫 `codex exec --full-auto`）
- 品質型：`qa-review`（`qaqa`：SDD spec + 雙軌測試 + 三方審查）

> 註：每個 skill 同時有 `SKILL.md` 與 `skill.md` 兩份（大小寫各一），內容相同，屬冗餘。

### 1.5 治理原則（SSOT / Governance）

- **SSOT**：所有交握記錄統一寫入 `log/`，命名格式固定，作為單一事實來源。
- 原始碼一律 `src/`、文件一律 `docs/`、日誌一律 `log/`。
- 環境規範：Git Bash（Unix 路徑）、`uv` 管理 venv、`.venv/Scripts/python` 執行。

---

## 2. 產物層架構（輸出成果）

> 這些是框架跑出來、或手動放進來的實際專案。彼此**大多不相關**。

### 2.1 `src/` — 多專案混居（注意！）

`src/` 並非單一專案，而是多次跑不同需求疊加的結果，可分為四群：

| 子系統 | 目錄 / 檔案 | 說明 |
|--------|-----------|------|
| **① Obsidian Ops Dashboard**（`pyproject.toml` 宣稱的主體） | `ai/`、`api/`、`indexer/`、`search/`、`pipeline/`、`sync/`、`vault_init/`、`config.py`、`organize_executor.py`、`tag_prefix.py` | Notion 風格 AI 知識庫 OS：匯入(Notion/Evernote/GDrive)→正規化→自動標籤→全文/語意/混合檢索→RAG QA→自動整理→Notion 雙向同步。FastAPI 服務。 |
| **② X1 跨系統契約整合**（對應 AUTOAI-ITS） | `models/`(factor/its/feedback/governance/export/observability/security/shared)、`governance/`、`observability/`、`security/`、`validators/`、`sandbox/`、`mock/` | DeepResearch ↔ Factor Platform ↔ ITS 的契約層、驗證器、治理板、可觀測性、安全（簽章/nonce/信任邊界）、沙盒回放。搭配根目錄 `contracts/`。 |
| **③ Roguelike 戰棋遊戲** | `battle_resolver.py`、`enemy_ai.py`、`formation_system.py`、`element_system.py`、`corruption_system.py`、`chain_system.py`、`ap_system.py`、`skill_system.py`、`turn_manager.py`、`upgrade_event.py`、`roguelike_manager.py`、`ui_renderer.py`、`input_handler.py`、`game_init.py`、`app.py`、`main.py` | 一個回合制 / Roguelike 遊戲引擎（與其他三群完全無關，疑為某次遊戲需求的產物）。 |
| **④ 股票儀表板** | `stock_dashboard/`(config/main/routes/services/validators) | 與根目錄獨立的 `stockpilot/` 子專案呼應的小型 web 後端。 |

> ⚠️ 這代表 `src/` 不能當成一個 coherent 套件看待——`pyproject.toml` 只描述 ①，但實體還含 ②③④。

### 2.2 `contracts/` — X1 契約資產（搭配 src ②）

版本化的契約資料：

- `X1.1/schemas/` — JSON Schema（export / factor / its / feedback / shared）
- `X1.2/examples/` — 完整範例情境（`taifex_tx_breakout_highvol`，台指期突破高波動場景），含 export→factor→its→feedback_bus→research_feedback 全鏈 payload
- `X1.3/tests/` — 測試套件（core / compatibility / negative）+ 無效 fixtures
- `X1.4/sandbox/` — 情境與回放（happy_path / factor_degradation / strategy_deviation）

### 2.3 獨立子專案（各自有 README/pyproject/venv）

| 子資料夾 | 性質 |
|----------|------|
| `obsidian-ops-dashboard/` | src ① 的完整獨立打包版（自帶 ops.* launcher、Dockerfile、DESIGN、docs） |
| `stockpilot/` | 獨立股票專案（migrations / templates / web / tests，自帶 pyproject + settings.yaml） |
| `COSTKILLER/` | 成本控制相關（DESIGN / apps / encode-decode 工具鏈） |
| `TOKENGUARD/` | TokenGuard Gateway（`tokenshield`，token 防護閘道） |
| `DQN project/`、`DNQ+/`、`DNQ+PLUS/` | DQN/Rainbow 強化學習量化研究（configs/runs/src，含交易策略） |
| `RAINBOWSIX_DNQ/`（+ `.7z`） | RainbowDQN 框架 + MA20/MA50 反彈策略 V1–V8（見 git log） |
| `vault/` | Obsidian vault 範本（`00_DASHBOARD`/`90_TEMPLATES`/`99_SYSTEM`） |

### 2.4 大型壓縮檔（佔空間，非程式碼）

- `RAINBOWSIX_DNQ.7z`（~322 MB）、`obsidian-ops-dashboard.7z`（~2.1 GB）、`obsidian-ops-dashboard-portable.zip`、`stockpilot.7z` — 都是打包備份，建議搬出版控範圍。

---

## 3. 技術棧

| 層面 | 技術 |
|------|------|
| 語言 | Python ≥ 3.11 |
| 套件/環境 | `uv`（venv 管理）、`pyproject.toml` + `requirements.txt` |
| Web | FastAPI + Uvicorn + sse-starlette（streaming） |
| CLI | Click（`ops.py` 統一入口；`ops-import` entry point） |
| 檢索 | Whoosh（全文）+ ChromaDB（向量）+ hybrid（0.4 全文 / 0.6 語意） |
| AI/LLM | OpenAI SDK；provider 可切 OpenAI / OpenRouter（`settings.yaml`）；tiktoken 計 token |
| 資料驗證 | Pydantic v2 + jsonschema |
| 整合 | Notion API、Evernote(.enex)、Google Drive API |
| 排程 | APScheduler（sync daemon，預設 15 min） |
| 測試 | pytest |
| 容器 | Dockerfile + docker-compose.yml |
| 編排 | Claude Code（agents/skills）+ OpenAI Codex CLI v0.113.0 |

---

## 4. 關鍵檔案地圖

```
codexAutoAI-master/
├── CLAUDE.md                  ★ 框架大腦規範（調用中心定義）
├── README.md                  ★ 事前準備（Claude / Codex 登入）
├── init.md                    Codex 環境驗證紀錄
├── .claude/
│   ├── agents/                ★ 7 個 sub-agent 定義
│   ├── skills/                ★ 9 組 skill（7 phase + codex-run + qa）
│   └── settings.json          專案層權限（Bash/Read/Edit/... 全 allow）
│
├── ops.py / ops.{bat,ps1,sh,zsh,command}   ← Obsidian 產物的統一 CLI launcher
├── settings.yaml              ← Obsidian 產物設定（AI/Notion/RAG/sync）
├── pyproject.toml             ← 宣稱 obsidian-ops-dashboard（僅描述 src ①）
│
├── src/                       ⚠ 多專案混居（① Obsidian ② X1 ③ 遊戲 ④ 股票）
├── contracts/X1.{1,2,3,4}/    X1 契約 schema / 範例 / 測試 / 沙盒
├── docs/                      需求/架構/報告（本文件即在此）
├── log/                       SSOT 執行日誌
├── vault/                     Obsidian vault 範本
├── tests/                     pytest 測試
│
└── （獨立子專案）obsidian-ops-dashboard/, stockpilot/, COSTKILLER/,
     TOKENGUARD/, DQN project/, DNQ+/, DNQ+PLUS/, RAINBOWSIX_DNQ/
```

---

## 5. 觀察與建議（風險點）

1. **身分不一致**：根目錄的 `CLAUDE.md`/`README.md` 描述的是「框架」，但 `pyproject.toml`/`settings.yaml`/`ops.py` 描述的是「Obsidian 產物」。閱讀者容易誤解專案本體。建議在根 README 明確區分「框架 vs 產物」。
2. **`src/` 混居**：①②③④ 四個不相關專案共用 `src/`，`pyproject.toml` 只涵蓋 ①。建議將 ②③④ 拆到各自目錄／repo，或至少在 docs 標明歸屬。
3. **Skill 冗餘**：每個 skill 同時存在 `SKILL.md` 與 `skill.md`，內容重複，建議擇一。
4. **大型壓縮檔入庫**：`.7z`/`.zip` 合計數 GB，拖慢 clone，建議移出版控或用 LFS。
5. **治理 vs 自動化張力**：`CLAUDE.md` 禁止自動 commit/push，但 Phase 流程強調「全自動不停」。實作時須靠權限層而非流程層守住這條紅線。

---

*本文件由架構分析自動產出，存放於 `docs/`，符合框架「文件一律 docs/」原則。*
