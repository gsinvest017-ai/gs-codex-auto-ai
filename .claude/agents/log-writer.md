# Log Writer — 日誌記錄規範

所有 agent 在執行過程中必須遵守此日誌記錄規範。

---

## 日誌目錄

所有日誌寫入 `log/` 資料夾。

`log/` 資料夾由 Phase 0（專案初始化）自動建立（`mkdir -p log`）。若寫入日誌時發現 `log/` 不存在，應先建立再寫入。

---

## 檔案命名規則

**CRITICAL（OBS-R1）：時間戳由系統時鐘產生，agent/LLM 嚴禁自行填寫時間。**

```
{system-timestamp}-{phase}-{描述}.md
```

- `{system-timestamp}` 必須來自 shell `date` 指令或 `clock.now_iso()` 的回傳值。
- 禁止由模型猜測或手動填入任何時間字串（例如 `{YYYYMMDD-HHmmss}` 佔位符）。

正確取法（shell）：
```bash
TS=$(date +"%Y%m%d-%H%M%S")
LOGFILE="log/${TS}-phase2-requirements.md"
```

正確取法（Python）：
```python
from codexautoai_v2.clock import now_iso
ts = now_iso()          # e.g. "20260619-143052"
logfile = f"log/{ts}-phase2-requirements.md"
```

範例（由系統產生）：
- `20260310-143052-phase1-env-check.md`
- `20260310-143120-phase2-requirements.md`
- `20260310-143305-phase3-architecture.md`
- `20260310-143502-phase4-codex-review.md`
- `20260310-143600-phase5-build-FN-001.md`

---

## 雙層日誌架構

v2 採用雙層日誌：

| 層級 | 格式 | 用途 |
|------|------|------|
| 主層 | JSONL（結構化事件） | 機器可讀、可查詢、可計算成本 |
| 次層 | Markdown 摘要 | 人工閱讀與 phase 交接 |

**主層（JSONL）由 `events.EventBus` 寫入，次層（Markdown）由 log-writer agent 負責。**

---

## 主層：結構化 JSONL 事件（OBS-R2）

每個事件依循 OpenTelemetry GenAI 慣例，寫入對應的 `.jsonl` 檔（如 `log/events.jsonl`）：

```jsonl
{
  "timestamp": "<clock.now_iso() 產生>",
  "event_type": "phase_start|phase_end|agent_call|codex_call|error|loop_tick",
  "phase": "phase2",
  "agent": "requirements-analyst",
  "status": "success|failure|in_progress",
  "gen_ai.request.model": "codex-...",
  "gen_ai.usage.input_tokens": 1234,
  "gen_ai.usage.output_tokens": 567,
  "iteration": 1,
  "retries": 0,
  "duration_ms": 4200,
  "defect_set_size": 0,
  "cumulative_cost_usd": 0.012
}
```

規則：
- 所有時間欄位由 `clock.now_iso()` 填入，禁止模型自行產生。
- `gen_ai.*` 欄位盡可能填寫；無法取得時填 `null`，不得省略欄位。
- 每次 Codex 呼叫（含 retry）各寫一筆事件。

---

## 次層：Markdown 摘要格式

每個 phase 完成後由 log-writer agent 寫入 `.md` 摘要。

**時間欄位取自系統時鐘（OBS-R1），不可由模型猜測。**

```markdown
# {Phase 名稱} — {描述}

## 時間
- 開始：{clock.now_iso() 或 shell date 取得的值}
- 結束：{clock.now_iso() 或 shell date 取得的值}

## 觸發者
{Dispatcher / 哪個 agent}

## 輸入摘要
{本次接收到的輸入描述（見 OBS-R4：不記錄完整 prompt/completion 內容）}

## 執行過程
{執行了什麼步驟、調用了什麼指令}

## 輸出摘要
{本次產出的摘要（不含原始 LLM 回應全文）}

## 迴圈指標（OBS-R3）
- iteration：{第幾輪}
- defect_set_size：{缺陷集大小}
- cumulative_cost_usd：{累計成本}

## 狀態
{通過 / 不通過 / 完成 / 失敗}

## 備註
{額外說明，如無則寫「無」}
```

---

## 交握記錄

每次 agent 之間的交握（一個 agent 的輸出傳給下一個 agent 的輸入）必須在日誌中完整記錄：

- **誰傳給誰**：來源 agent → 目標 agent
- **傳了什麼**：交握摘要（見 OBS-R4，不記錄原始 prompt 全文）
- **何時傳的**：由 `clock.now_iso()` 取得的精確時間

---

## v2 可靠性對齊

本節說明 v2 新增的五項強制規則，對應 `DESIGN/.../specs/observability/` 與 `security-governance/`。

### OBS-R1 — 系統時鐘 SSOT（CRITICAL FIX）

**背景**：舊版允許模型直接填寫 `{YYYYMMDD-HHmmss}`，導致時間為 LLM 捏造值，破壞 SSOT 排序與稽核一致性。

**規則**：
- 所有時間戳（檔名、事件欄位、Markdown 時間欄）一律由 shell `date` 或 `clock.now_iso()` 取得。
- agent/LLM 嚴禁自行產生任何時間字串。
- 若無法取得系統時間，應記錄錯誤，不得填入猜測值。

### OBS-R2 — 結構化 JSONL 為主日誌

- `events.EventBus` 為主日誌寫入者。
- 欄位遵循 OpenTelemetry GenAI 慣例（`gen_ai.request.model`、`gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens` 等）。
- Markdown 摘要為次層，不可取代 JSONL。
- **phase 邊界事件由協調引擎強制寫入**：每個 phase 一律用 `with orchestrator.phase("phaseN"):` 包住，由 `Orchestrator.phase()`（`orchestrator.py`）在進入時寫 `phase_start`、離開時寫 `phase_end`（`status=success/failure`）。此為確定性程式碼所有，**不依賴 LLM 記得寫**，因此 `tools/progress.py` 的進度條保證會推進。

### OBS-R3 — 迴圈指標可觀測

- 每次 loop tick 必須記錄：`iteration`、`defect_set_size`、`cumulative_cost_usd`、`duration_ms`。
- 目的：事後可還原終止守衛（termination guard）的觸發條件。

### OBS-R4 + SECGOV-R3 — 隱私保護與機密脫敏

- **不記錄**完整 prompt 或 LLM completion 原文（隱私預設）。
- 所有日誌在寫入前必須通過 `events.redact()` 脫敏。
- 脫敏對象：API keys、tokens、passwords、任何符合 secret 模式的字串。
- Markdown 摘要同樣遵守此規則：只寫摘要，不寫原文。

### SECGOV-R8 — 稽核日誌僅可追加、密碼學防竄改

- 稽核日誌由 `audit.AuditLog` 維護，採用雜湊鏈（hash chain）確保防竄改。
- 每筆稽核記錄含前一筆的雜湊值；任何竄改均可被偵測。
- **禁止**以任何方式修改或刪除已寫入的稽核記錄（舊版「不可事後修改」現由密碼學強制執行，非僅靠慣例）。
- log-writer agent 不直接寫入稽核日誌，只調用 `audit.AuditLog.append()`。

---

## 原則摘要

| 原則 | 說明 |
|------|------|
| 系統時間 | 所有時間戳來自系統時鐘，模型嚴禁自填 |
| JSONL 優先 | 主層為結構化事件，Markdown 為次層摘要 |
| 迴圈可觀測 | 每輪記錄 iteration / defect size / 累計成本 |
| 隱私脫敏 | 不記錄原始 prompt/completion，脫敏後寫入 |
| 防竄改稽核 | 稽核日誌雜湊鏈，僅可追加，密碼學保證 |
| 每 phase 至少一筆 | 每個 phase 至少一個日誌檔案 |
| 失敗也要記 | 失敗操作必須記錄，含錯誤訊息 |
