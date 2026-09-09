# Workbench 與 MCP（0.15）

`tools/workbench.py` 是 UI、CLI 與 MCP 共用的工作區資料入口；路由呼叫 `model_router`，完成的用量指標呼叫 `events_model.routing_stats`。它不啟動模型、不更改路由、不修改全域設定。

## CLI / Python API

```powershell
python tools/workbench.py --root C:/Projects/demo --action catalog
python tools/workbench.py --root C:/Projects/demo --action preview --prompt "建立3D角色"
python tools/workbench.py --root C:/Projects/demo --action status
python tools/workbench.py --root C:/Projects/demo --action activity --limit 100
python tools/workbench.py --root C:/Projects/demo --action artifacts
python tools/workbench.py --root C:/Projects/demo --action register_artifact --path assets/model.glb --label "第一版"
python tools/workbench.py --root C:/Projects/demo --action report_progress --run-id RUN_ID --message "已建立初稿" --progress 35
```

Python 使用 `Workbench(root).catalog()/preview(prompt, scenario=None)/status(run_id=None)/activity(run_id=None, limit=100)/artifacts()/report_progress(message, run_id=None, progress=None)/register_artifact(path, run_id=None, label=None)`，均回傳 JSON 可序列化 dict。

- `status.task_status` 驗證同 run 的 `task-result-*.json` schema、起訖時間、本次 Phase 7 success 與 artifact SHA-256 格式，與 dashboard 的 `taskResult` 契約一致。舊 `app-run.json` 單寫 completed 而缺交付證據會降為 incomplete；缺資料為 unknown。`call_status` 是模型呼叫狀態，ok 不代表任務完成。交付後不重算使用者修改過的檔案 hash，因此歷史完成狀態不會因後續編輯失效；runner 在原交付時已驗證 hash。`task_result.source/evidence_validation` 提供判定來源。
- `activity.items` 包含 `id/attempt_id/item_id/event_type/provider/kind/text/summary/status/historical/source_path/source_line/source_offset/timestamp/source_mtime`。同 attempt 的同 item ID 保留最新事件。無 ID 的事件是歷史觀察；不推測它仍在執行。
- `timestamp` 只採 CLI 事件原有時間；缺少時為 null。`last_update` 與 `source_mtime` 為來源檔案 mtime（epoch 秒），不是單筆事件發生時間。
- 每次每個 CLI log 最多讀末尾 4 MiB，最多回 200 筆活動。`limited` 代表有截斷；截斷時全呼叫 usage 保持未知，不以局部用量冒充完整總量。CLI 未回報的模型／tokens 保持 null。只呈現公開 assistant message、工具、命令、檔案變更；不呈現 reasoning/thinking blocks。
- `artifacts.items` 是 root 內 GLB/glTF/OBJ/PNG 的實際檔案；含合法 registry 檔案。掃描有目錄／檔案數上限，排除 `.git/.venv/venv/vendor/.claude/.codex/node_modules/log` 等目錄。檔案經 realpath confinement；symlink/junction 不能帶出工作區。
- `log/workbench-artifacts.jsonl` 只保存版本登記 metadata（SHA-256、時間、來源路徑），`snapshot=false`；原檔覆寫後不能用這份紀錄還原。它不是持久檔案快照。
- `report_progress` 寫入獨立 `log/workbench-progress.jsonl`，標記 `source=caller_reported, verified=false`，不改 phase、任務完成狀態或 tokens。

## MCP stdio

```powershell
python C:/path/to/codex-auto-ai/tools/workbench_mcp.py --root C:/Projects/demo
```

預設提供 `scene_catalog`、`route_preview`、`run_status`、`run_activity`、`list_artifacts` 五個唯讀工具。只有啟動指令明確加 `--allow-reports`，才額外提供 `report_progress` 與 `register_artifact` 兩個 append-only 登記工具。

資源 URI 為 `workbench://catalog`、`workbench://status`、`workbench://activity`、`workbench://artifacts`，以 JSON text resource 回傳。

協定版本 `2025-06-18`；支援 initialize → notifications/initialized、ping、tools/list、tools/call、resources/list、resources/read。stdio 每行一份 UTF-8 JSON-RPC；stdout 只輸出協定訊息。沒有 HTTP listener、訂閱或模型執行工具。依據 [MCP stdio](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)、[生命週期](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)、[tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) 與 [resources](https://modelcontextprotocol.io/specification/2025-06-18/server/resources) 規範。

## 手動註冊片段

請把範例路徑替換成實際安裝位置及單一工作區。下列只是範例；本次實作不會執行註冊或修改設定。

VS Code 的 workspace `.vscode/mcp.json`：

```json
{
  "servers": {
    "codexautoai-workbench": {
      "type": "stdio",
      "command": "python",
      "args": ["C:/path/to/codex-auto-ai/tools/workbench_mcp.py", "--root", "C:/Projects/demo"]
    }
  }
}
```

[VS Code 官方設定說明](https://code.visualstudio.com/docs/agent-customization/mcp-servers)。

Claude Code 的專案 `.mcp.json`：

```json
{
  "mcpServers": {
    "codexautoai-workbench": {
      "command": "python",
      "args": ["C:/path/to/codex-auto-ai/tools/workbench_mcp.py", "--root", "C:/Projects/demo"]
    }
  }
}
```

[Claude Code 官方 MCP 說明](https://code.claude.com/docs/en/mcp)。

Codex 的專案 `.codex/config.toml`：

```toml
[mcp_servers.codexautoai_workbench]
command = "python"
args = ["C:/path/to/codex-auto-ai/tools/workbench_mcp.py", "--root", "C:/Projects/demo"]
```

[Codex 官方 MCP 說明](https://learn.chatgpt.com/docs/extend/mcp)。亦可由使用者自行執行 `codex mcp add`；本機 `codex mcp add --help` 已確認支援 `NAME -- COMMAND...` 格式。

## Watchdog 證據

Runner 現在於子行程啟動前將其專屬 `result_path` 寫入同 attempt 的 started 更新，UI 可在執行中讀取。心跳採本次 CLI 輸出增長，以及該輸出 `thread.started` 指定之 thread／有明確 `parent_thread_id` 關聯子孫的 session 更新。其他任務的最新全域 session 不會讓掛死呼叫保持存活。硬 timeout 仍然生效；沒有可驗證的輸出或關聯 session 時，不推測 agent 在工作。
