# 模型路由與執行證據

`tools/model_router.py` 是 VS Code 與 runner 共用的純標準庫政策解析器。預覽只解析設定，不呼叫模型，也不證明模型已執行。

## 預設與額度備援

主線只有 Codex 與 Claude。`codex-first` 預設各場景使用 Codex；`multi-provider` 將 research / review 改為 Claude，其餘使用 Codex。3D 場景預設指定 `gpt-6-astra`；其餘 model 為 null 時沿用 CLI 設定。

只有本次 runner invocation 先取得兩個主線 provider 的明確 `quota_exhausted` 結果，才允許呼叫 OpenCode。一般 429 / rate limit、登入失敗、CLI 缺失、逾時、無效模型、普通執行錯誤都不能當成額度耗盡。沒有以歷史用量百分比推測可開啟備援。

舊設定或明確參數若選 Gemini / DeepSeek / OpenCode，仍會先走 Codex 主線並記錄原始 requested provider；不會繞過雙主線額度閘門。OpenCode 使用設定的 `provider/model` ID；備援模型可在控制台修改。CLI 與登入仍需在本機可用，政策不保證模型授權。

```powershell
python tools/model_router.py --root . --catalog --json
python tools/model_router.py --root . --prompt "review Blender addon" --json
python tools/model_router.py --root . --save-route --scenario research --provider claude --json
python tools/model_router.py --root . --save-route --fallback-model google/gemini-2.5-pro --json
python tools/codex_runner.py --cwd . --prompt "審查程式" --scenario review
```

範例中的備援模型 ID 僅示範設定格式，需改成該機 OpenCode 實際提供的 ID。

## UI 與執行的關係

控制台提供場景、主線 provider、模型及備援模型設定，接線圖呈現主線與額度條件。政策存於專案 `log/model-routing.json`，runner 讀取同一設定。變更設定影響後續呼叫；既有 attempt 不會被改寫為新的預定路線。

實際結果以 `log/events.jsonl` 的 `type=model_attempt` 為準，每筆帶 `run_id`、`attempt_id`、requested / actual provider 與 model、configured_model、scenario、reason、outcome、duration_ms、usage 與 usage_source。started 和 terminal 共用 attempt_id。runner JSON 的 run_id 可對回事件；requested、configured 與 CLI 真正回報的 actual_model 不混用。CLI 沒回報模型時 actual_model 保留 null。

`tools/events_model.py` 的 `routing_stats` 與 dashboard `summarizeRoutingAttempts` 有完整 JSON parity 測試。新模型的 `routing` 欄位與 UI 的路由證據遵循相同規則：

- 依 parent_run_id 彙總同一 App pipeline 的多個 worker invocation；沒有 parent 時按 run_id。只統計有 attempt_id 的 `ok`、`failed`、`quota_exhausted` 終結紀錄；preview / started 不算已完成呼叫。
- 同一 run_id + attempt_id 重播或重複寫入只計一次；未指定範圍時選最新 attempt 所屬 parent run 或單一 invocation。不同 worker 的額度證據不可互借。
- tokens 只取 attempt 回報的 usage；各欄完全未知時為 null；inKnown / outKnown / cacheKnown / costKnown 分別記錄該欄有數值的 attempt 數。少於 attempts 時合計只能標示為「已知部分」，並顯示未知筆數，不能宣稱完整總量、免費或零使用量。沒有 cost_usd 時 cost 為 null。
- OpenCode 之前缺少同 run 的 Codex 與 Claude quota_exhausted 紀錄會產生 violation；不能靠畫出的預定接線推定實際遵循政策。
- 舊 session / transcript 統計是歷史觀測，不與本次 attempt metrics 相加；session 累積用量必須依事件時間取區間差額，reasoning_output_tokens 不再重加至 output_tokens。

`observed` 代表有終結事件可觀測，不保證每次成功。`unverified` 代表沒有可用的終結證據。模型自報 usage 不是供應商帳單或訂閱剩餘額度。

## 執行限制與驗證

本機 Codex CLI 0.153.4 已不接受舊的 `--full-auto`；runner 使用 `--sandbox workspace-write`，並保留 stdin=DEVNULL、watchdog、重試與 `--expect` 產物驗證。非 Codex 結果保存在 `log/model-routing-results/`。需要產物時備援走 writer 角色，仍必須驗證要求的產物存在。Claude 寫入授權只限有前序同 invocation Codex quota_exhausted 證據的當次 writer attempt；環境標記須匹配 events.jsonl 中未過期的 started 紀錄，terminal 立即撤銷。沒有全域關閉守門或 AskUserQuestion 保護。此機制是流程控制，不是防範能自行修改本機日誌與環境的安全邊界。

```powershell
python -m pytest tests/tools/test_model_router.py tests/test_codex_runner.py tests/tools/test_events_model.py tests/tools/test_dashboard_parity.py -q
```

測試以假 CLI 子程序產生成功、明確額度耗盡、一般錯誤、token 回報等可重現輸出，檢查 runner 真正啟動順序、argv、事件與 UI 彙總。假程序不消耗模型額度，也不能代替真實供應商驗證。本次環境 OpenCode 不在 PATH，因此沒有宣稱完成 Gemini / DeepSeek 經 OpenCode 的實機付費呼叫驗證。

## Windows 子行程啟動修復（0.14.2）

2026-09-09 的實際任務在 Phase 1 選到 `Microsoft\WindowsApps\pwsh.exe` 執行別名，由受限子行程啟動時得到 `CreateProcessAsUserW failed: 5`。這是環境啟動失敗，不能當成額度耗盡，也不能因模型最後正常退出而宣告交付完成。

Windows 上的 Codex runner 現在只調整該次子行程的環境副本：從 PATH 排除 `Microsoft\WindowsApps` 別名目錄，保留實體程式路徑並補入系統 WindowsPowerShell 路徑。若 PATH 有實體 `pwsh.exe` 仍可使用；沒有時 Codex 可選系統 `powershell.exe`。單次 CLI 同時使用 `-c allow_login_shell=false`，避免 PowerShell profile 的額外啟動副作用。此修復不變更全域 PATH、Codex config、ACL 或 `workspace-write` sandbox；非 Windows 行為保持原狀。`allow_login_shell` 語意依據 [OpenAI 官方設定文件](https://learn.chatgpt.com/docs/config-file/config-reference)。

真實驗證在原測試工作區的 `log/shell-selftest-20260909` 進行，目錄由 PowerShell `New-Item` 建立並繼承正常 ACL。刻意把 WindowsApps 放到 PATH 最前面、移除 Codex App 附帶 PowerShell 路徑後，再透過 runner 呼叫真實 Codex，要求只用預設 shell 寫入、讀回檔案及執行 Git/Python，禁用 Node 檔案寫入替代方案。

- invocation：`cd00d0a239504d15adf9fe22dc890438`；1 次呼叫、21.6 秒，未觸發備援。
- 真實 `command_execution` 使用 `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile`，exit code 0。
- `smoke.txt` 原始位元組精確為 `SHELL_WRITE_OK`；Git 2.53.0.windows.1、Python 3.12.10 均 exit code 0。
- 證據：`log/shell-selftest-20260909/log/model-routing-results/codex_8_kqmnru.log`。此為 shell 啟動與讀寫驗證，並不代表原機器人任務已完成七階段交付。

`tests/tools/test_runner_shell.py` 覆蓋 Windows 別名排除、大小寫與斜線、保留實體工具 PATH、不修改原始環境與 `os.environ`、非 Windows 不變，以及保留 sandbox 的 CLI 參數。
