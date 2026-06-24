# CodexAutoAI 系統審查（REVIEW5）

> 審查日期：2026-06-19 · 範圍：系統框架本身（分支產物排除）
> 維度：正確性 / 健壯性 / 可擴展性 / 一致性·治理 / 可維護性·可觀測性
> 後續：[競品研究](codexAutoAI-competitive-research.md) → [v2 SPEC](../DESIGN/README.md)

## 評級總覽

| # | 發現 | 嚴重度 | 維度 | v2 解法 |
|---|------|--------|------|---------|
| 1 | Phase 4 / 6 循環無上限、無 escalation | **P0** | 健壯性 | `orchestration`：max-iter + no-progress + budget + escalation |
| 2 | Codex 同時是審查者與實作者，審查非獨立 | **P0** | 正確性 | `review`：跨模型 CRITIC（錨定測試/編譯） |
| 3 | 日誌 timestamp 由 LLM 自填 → SSOT 排序不可信 | **P0** | 一致性 | `observability`：shell 戳記，禁模型自填 |
| 4 | Phase 5 並行寫檔 race condition（多 FN 同檔） | **P0** | 正確性 | `parallel-build`：worktree 隔離 + 所有權切分 |
| 5 | Build 後無「程式碼 vs FN 規格」審查 | **P1** | 正確性 | `review`：屬性驗證 gate |
| 6 | `codex --full-auto` ＋ 全域權限全放行 = 無閘門 | **P1** | 治理/安全 | `safety`：沙箱 + deny 不可逆 + 事後稽核 |
| 7 | 硬編路徑 `/c/Users/User/...`（實際是 `USER`） | **P1** | 健壯性 | `safety`：動態解析環境 |
| 8 | 測試用 `&`+`sleep 3`+`kill %1`，並行全綁 :8000 | **P1** | 健壯性 | `parallel-build`：動態 port + health poll |
| 9 | 無 checkpoint／resume，中途斷掉整條重跑 | **P1** | 可維護性 | `state-resume`：action-level checkpoint |
| 10 | 依賴圖無環檢測 → 循環依賴卡死分批 | **P1** | 正確性 | `orchestration`：拓撲排序守門 |
| 11 | 專案類型誤判無校驗閘門，錯誤一路放大 | **P1** | 正確性 | `spec-authoring`：類型確認 gate |
| 12 | `SKILL.md`/`skill.md` 雙份（Windows 恐互蓋） | **P2** | 可維護性 | 清理留一份 |
| 13 | 日誌散文化，無結構化指標 | **P2** | 可觀測性 | `observability`：JSONL gen_ai.* |
| 14 | 產出 web/auth 專案卻無安全審查 phase | **P2** | 安全 | `review`：安全 gate |

## P0 詳述

**① 循環無界**：`dispatcher.md`/`phase4`/`phase6` 皆「循環直到通過」，無 MAX_RETRY、無逃生口 → 無限燒 token。
**② 審查非獨立**：`codex-reviewer` 與 `function-builder` 都用 `codex exec` = 同模型自審，盲點相關。Dispatcher 複審也是後續同一個 Claude（self-review）。
**③ timestamp 幻覺**：`log-writer.md` 要求 `{YYYYMMDD-HHmmss}` 由模型填，但 LLM 不可靠知道真實時間 → 整個 SSOT 排序建立在捏造時戳上。
**④ 並行寫檔競爭**：`architecture-planner` 對單檔專案把多 FN 放同一 `src/x.py`；多 builder 並行 `codex exec` 同檔互蓋。分批只解呼叫依賴，未解檔案寫入衝突。

## P1 詳述

**⑤** Phase 4 只審架構，Phase 5 寫完直接進測試，無 code↔spec 審查。
**⑥** `function-builder` 自帶「Codex 寫到根目錄就搬回 src/」步驟＝承認 Codex 越界；配全域全放行＝無閘門。
**⑦** `/c/Users/User/.local/bin/uv` 等硬編路徑；實際帳號是 `C:\Users\USER`（大寫）。
**⑧** `uvicorn &`+`sleep 3`+`kill %1` 在 Git Bash 不穩；並行 test-runner 全綁 :8000 衝突。
**⑨** 中途斷無 checkpoint，只能整條重跑。
**⑩** 依賴圖無環檢測，有環卡死。
**⑪** 類型誤判（如 quant 當 web）連鎖污染架構＋測試＋報告，單點故障無校驗。

## 系統做對的地方

大腦/寫手職責分離清楚可審計 · 契約驅動流水線（F↔FN 一一對應）紮實 · 類型自適應（11 型）優雅 · 「實際執行測試非僅 import」比多數 codegen 框架嚴謹。

## 總評

> 架構骨架是 A 級，但運行時健壯性是最大的洞——4 個 P0 全集中在「循環無界、審查不獨立、時戳造假、並行寫檔競爭」，都會在真實長流程跑壞且現無防護。先補 P0①②③④。
