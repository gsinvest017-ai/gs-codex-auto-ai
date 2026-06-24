---
name: autopilot
description: "非停執行模式：開啟後連『回合結束等使用者』都不停，自動把 CodexAutoAI pipeline 跑到底。當使用者輸入 /autopilot on <需求> / off / status，或說『全程不要停』『一路做到交付不要問我』時使用。"
---

# /autopilot — 非停執行模式（per-session）

讓 pipeline 連「自己結束回合等人」都不停，一路自動跑到 Phase 7 交付或守衛 escalate。
**權限提示**本來就由本 repo 預設的 `bypassPermissions` 處理（不問）；本模式額外處理「回合續跑」。

## 用法
- `/autopilot on <需求>`：開啟。建議把完整需求一次講清楚，這樣 Phase 2 沒東西要澄清、就能真的不停。
- `/autopilot off`：關閉。
- `/autopilot status`：看目前第幾輪 / 上限 / 任務。

## 機制（你不需要手動做這些）
- `on/off/status` 在你看到 prompt 之前，已由 UserPromptSubmit hook `tools/autopilot/arm.py` 攔截處理：
  用 **hook 拿到的真實 session_id** 綁定旗標到 `log/autopilot/<session_id>.json`。
- 每次你想結束回合時，Stop hook `tools/autopilot/cont.py` 會把你擋回去繼續，直到：
  完成（建立 `log/autopilot/<session_id>.done`）、達續跑上限（30 次）、或本 session 沒開 autopilot。
- **per-session 獨立**：同時開多個本 repo 的 session，各自的 autopilot 互不干擾。

## 規則
- **旗標由 hook 綁定，不要自己寫 `log/autopilot/*.json`**（會覆蓋 hook 綁好的 session_id，破壞多 session 隔離）。
- **保留憲章安全停點**：即使在非停模式，遇到 (a) Phase 2 需求需澄清、(b) C6 不可逆操作（`commit`/`push`/刪除）待授權、(c) 守衛 escalate —— 仍應停下；停之前先 `touch log/autopilot/<session_id>.done` 讓 Stop hook 放行。
- 完成整個 pipeline（Phase 7 交付）後，建立 done sentinel 結束非停模式。
