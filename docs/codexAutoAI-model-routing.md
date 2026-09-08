# 智慧模型路由

`tools/model_router.py` 是 VS Code 與 runner 共用的純標準庫決策器。這是本機可覆寫的啟發式政策，不是模型能力或帳號授權保證。預設一般任務沿用 Codex CLI 模型設定，3D / Blender / mesh / 三維 / 建模任務選 `codex` + `gpt-6-astra`。模型若帳號不支援會明確失敗，不偷偷降級。

## 預览與執行

```powershell
python tools/model_router.py --root . --prompt "用Blender製作角色" --json
python tools/codex_runner.py --cwd . --prompt "用Blender製作角色並寫入src" --expect src/character.py
python tools/codex_runner.py --cwd . --prompt "review the Blender addon code" --scenario review
```

預覽回傳 `scenario/provider/model/reason`。`model: null` 表示沿用該廠商 CLI 的本機模型設定。預覽不啟動 CLI、不耗模型額度，也不驗證登入或模型可用性。

實際 runner 回傳 `status/attempts/duration_s/reason/route`。Codex 保留 stdin=DEVNULL、session grace、心跳 watchdog、重試與 `--expect` 驗證。所有 provider 每次嘗試受 `--timeout`（預設 1800 秒、必須正有限值）限制。不存在的 CLI 在啟動前失敗，登入/模型錯誤由輸出或退出狀態回報。

## 多廠商預設組合（需主動啟用）

```powershell
python tools/model_router.py --root . --preset multi-provider --json
```

此操作寫入專案 `log/model-routing.json`；既有設定先保留為同目錄唯一時間戳備份。`--preset codex-first` 可恢復全部 Codex 的預設組合，同樣保留備份。

| 場景 | 多廠商組合 | 執行契約 |
|---|---|---|
| 3d_modeling | Codex / gpt-6-astra | 可產出檔案 |
| coding / debugging / documents | Codex / CLI 預設模型 | 可產出檔案 |
| review | Claude / CLI 預設模型 | 唯讀，Read/Glob/Grep/WebSearch/WebFetch 工具 |
| research | Gemini / CLI 預設模型 | 唯讀 plan 模式 |

Claude 與 Gemini 的完整 CLI stdout/stderr 保存在 `log/model-routing-results/` 唯一檔，runner JSON 的 `result_path` 指向原文；同名 `.meta.json` 保存路由、模型設定、成功/失敗與時間。輸出可能是 JSON 或含 CLI 診斷的文字，請以實際內容解析。模型回覆不直接塞入控制台 JSON。

非 Codex provider 有 `--expect` 時採兩段式：先唯讀分析並驗證非空有效結果，再由 Codex writer 讀取結果、核對原任務並產出指定檔案。分析失敗不啟動 writer；writer 失敗整體仍失敗。writer 使用 Codex CLI 預設模型，不會誤用 Claude/Gemini 模型。JSON 另回 `writer_route` 與 `writer_attempts`。每階段各有 `--retries` 上限（線性相加，預設最多分析 3 次 + writer 3 次），每次嘗試各受 `--timeout` 限制；writer 重試不會重跑成功的分析。原有 Codex-first 寫入守門與 AskUserQuestion 保護維持原樣，沒有透過環境變數停用。Worker 有直接完成指定任務、不可再次分派的指令；子程序 marker 使遞迴 runner 呼叫明確失敗。這是程序角色控制，不是惡意本機程序的安全邊界。

Claude 使用 `--output-format json --tools Read,Glob,Grep,WebSearch,WebFetch`；Gemini 使用 `--output-format json --approval-mode plan`。兩者都沒有新增 bypass/yolo。安裝與登入需由各 CLI 既有方式完成；測試使用假程序，不消耗實際帳號額度。

## 自訂政策

```json
{
  "default": {"provider": "codex", "model": null},
  "scenarios": {
    "3d_modeling": {"provider": "codex", "model": "gpt-6-astra"},
    "research": {"provider": "gemini", "model": null},
    "review": {"provider": "claude", "model": null},
    "my_domain": {
      "provider": "codex", "model": null,
      "keywords": ["CAD裝配"], "priority": 10
    }
  }
}
```

內建場景覆寫會保留未提供的 keywords；新場景可指定 keywords，或僅用 `--scenario` 明確選取。priority 越大越先處理，相同 priority 先看句首任務意圖（例如 review/research），再使用內建順序：3D、debugging、review、research、documents、coding。設定格式錯誤會失敗而非默默忽略。沒有隱式 fallback。

優先順序：明確 `--model` 最大（未指定 `--provider` 時保留歷史語義：Codex）；明確 `--provider` 換廠商時清除原廠商模型；`--scenario` 固定場景；否則依 prompt 分類。要固定一般配置可用 `--scenario default`。

VS Code 透過子終端機環境 `CODEXAUTOAI_TASK_PROMPT` 保留原始需求。runner 先分類當前子任務：明確 review/research/documents 優先；當前 default 可回退原需求，coding/debugging 只有父需求是 3D 時才繼承 Astra。所有明確 CLI 選擇都阻止此回退。例如父任務建模、子任務「實作函式」仍選 Astra，子任務「review Blender code」選 review。CLI 使用者也可明確傳 `--scenario 3d_modeling` 避免上下文遺失。

分類器會排除否定子句和已知非空間建模詞（ML、數學、金融、資料、tensor）；中英混寫可識別。泛稱「建模」仍是為使用者需求保留的 3D 啟發式，無法保證所有語言語意，遇到歧義應使用明確場景或自訂政策。`review the Blender addon code` 選 review；`build Blender model and document it` 選 3D。

## 驗證

```powershell
python -m pytest tests/tools/test_model_router.py tests/test_codex_runner.py -q
```

涵蓋分類反例、手動選擇、設定繼承/priority、真實 adapter argv、缺少 provider、Codex watchdog、非 Codex 逾時/structured error/結果保留與唯讀限制、兩段式任一失敗不可成功。這些不宣稱完成真實付費模型的端到端生成驗證。
