---
name: progress
description: "顯示 CodexAutoAI pipeline 目前進度（phase 進度條 + 當前迭代 + 累計成本）。當使用者輸入 /progress、說「現在跑到哪」、「進度如何」、「卡在第幾階段」、「目前 phase」時啟動。"
---

# /progress — 顯示目前 pipeline 進度

讓使用者在對話視窗主動查看目前 run 跑到哪一個 phase，不必另開終端機。

> ⚡ **bare `/progress`（不帶參數）由 `tools/progress_hook.py`（UserPromptSubmit hook）即時回覆、不進模型**（零 LLM round-trip，避免 ~11 秒延遲）。下面的步驟是 hook 未啟用時的後備，或 `/progress --watch` 等帶參數情況才會跑到。

## 執行

優先順序：

1. **若本回合 context 已有 `[codexautoai-progress]…[/codexautoai-progress]` 區塊**
   （由 `tools/progress_hook.py` 這個 UserPromptSubmit hook 注入）：
   → 直接 **verbatim echo** 該區塊內文（等同 autogo 的 ECHO PATH），不重跑、不加料。

2. **否則**（hook 沒注入，或使用者想要最新狀態）：
   → 跑 `python tools/progress.py` 取得當前進度條並顯示。
   （需要持續刷新時，告知使用者可在終端機跑 `python tools/progress.py --watch`。）

## 沒有進行中的 run 時

若 `log/events.jsonl` 不存在或無任何 phase 事件，`progress.py` 會回報「尚未開始」。
此時告訴使用者：目前沒有進行中的任務，打 `/start` 或描述需求即可啟動。

## 邊界

- 本 skill 只負責「顯示」，不啟動、不推進 pipeline（啟動見 `/start`）。
- 進度資料來自 `log/events.jsonl`，其中 phase 邊界事件由 `orchestrator.phase()` 確定性寫入，不依賴 LLM。
