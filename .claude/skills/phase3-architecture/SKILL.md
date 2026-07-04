---
name: phase3-architecture
user-invocable: false
description: "Phase 3：Claude 給 high-level 架構綱要，架構文件與 fn-manifest 由 Codex 產出。"
---

# Phase 3：系統架構規劃（Codex-first）

**分工鐵律**：Claude 只做 high-level planning 與驗收；架構文件的**撰寫全部交給 Codex**
（Codex 額度大、Claude 額度貴——見 CLAUDE.md「Codex-first 硬分工」）。

## Step 1：Claude 寫 high-level 綱要（≤15 行 bullet）

讀 `docs/requirements-spec.md`，只產出一份**極簡綱要**（不寫進檔案，直接放進 Step 2 的 prompt）：

- 架構模式（一句話，含理由）
- 模組切分（每模組一行：名稱 + 職責）
- 關鍵資料流（一句話）
- 並行分批的大方向

## Step 2：Codex 產出架構文件與 fn-manifest

把綱要 + requirements-spec 交給 Codex 寫完整文件：

```bash
python tools/codex_runner.py --expect docs/architecture.md --expect docs/fn-manifest.json --prompt "你是系統架構師。根據以下 high-level 綱要與 docs/requirements-spec.md，撰寫：
1. docs/architecture.md —— 完整系統架構文件：每個 function 的職責、介面（參數名稱/型別/回傳值）、依賴關係、並行分批計畫、每個 function 的測試方式。檔案路徑一律在 src/ 下。
2. docs/fn-manifest.json —— 機器可讀清單，格式：
   [{\"id\":\"FN-001\",\"file\":\"src/xxx.py\",\"deps\":[\"FN-002\"],\"signature\":\"def f(...)->...\",\"ears\":[\"FN-001-S1\"]}]
綱要：{Step 1 的綱要}"
```

## Step 3：中控驗收（只回 PASS/FAIL，不改寫）

檢查（≤10 行短評）：
1. 架構模式與專案類型匹配
2. Function 與需求一一對應（不多不少）
3. 每個 function 介面完整、路徑都在 `src/` 下、附測試方式
4. `docs/fn-manifest.json` 可被 JSON 解析、拓樸無循環

**FAIL → 把 findings 原文丟回 codex_runner 修**（最多 3 輪），Claude 不得自行 Edit/Write
`docs/architecture.md` 或 `fn-manifest.json`（PreToolUse hook 會擋）。

## 產出

- `docs/architecture.md`、`docs/fn-manifest.json`（**皆由 Codex 寫入**）
- 事件由 `run_phase.py begin/end` 寫入 `log/events.jsonl`

## 完成條件

驗收 PASS → **自動進入 Phase 4（`/phase4-review`）**
