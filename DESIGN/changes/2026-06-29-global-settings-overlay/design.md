# 啟動期全域設定 overlay（開 App 套用、關 App 還原）

## 目標

桌面 App / VS Code extension 啟動期間，把「full-auto 友善」設定**暫時**套到使用者全域設定，
App 一關就還原。讓開著 App 時任何地方跑的 Claude / Codex 都吃到免確認的自動化設定，不留痕跡。

## 套用範圍（使用者決策）

| 目標 | 檔案 | 動作 | 不碰 |
|------|------|------|------|
| Claude Code | `~/.claude/settings.json` | 整個 `permissions` 鍵換成 bypassPermissions + allow/ask/deny 安全網 | `hooks` / `env` / 其他鍵 |
| Codex | `~/.codex/config.toml` | 頂層 `approval_policy="on-failure"`、`sandbox_mode="workspace-write"`（標記區塊插檔首） | 任何 `[table]` 內的同名鍵、其餘內容 |

> Claude 的專案層 hooks 用 `$CLAUDE_PROJECT_DIR`，搬到全域會失效，故**刻意不套 hooks**。

## 機制

- 共用模組兩份同協定實作：`desktop/global_overlay.py`（launcher 用）、
  `vscode-extension/globalOverlay.js`（extension 用）。內容、state 檔、marker 完全鏡像，兩邊可互通。
- **觸發點**：desktop 在 `main()` acquire，`WM_DELETE_WINDOW` / `atexit` / `finally` 三重 release；
  extension 在 `activate()` acquire，`deactivate()` release。
- **多實例安全**：owner 引用計數，state 存 `~/.codexautoai/overlay-state.json`。
  第一個 acquire 才真的套用並存「原始值備份」；最後一個 release 才還原。
- **崩潰自癒**：每個 owner 綁 PID（+ 24h TTL）。acquire 時清掉 PID 已死或逾期的殭屍 owner，
  所以 App crash 沒走到 release，下次任一端啟動會把殘留設定收乾淨。
- **還原精準度**：key-level / 標記區塊 還原——只動自己加的東西，期間使用者對其他鍵的編輯不受影響。
- **安全閥**：環境變數 `CODEXAUTOAI_NO_GLOBAL_OVERLAY` 或 VS Code 設定 `codexautoai.applyGlobalSettings=false` 可整個停用。
- 所有函式不 raise——套用 / 還原失敗只記 log，絕不擋 App 啟動或關閉。

## 測試

`tests/test_global_overlay.py`（9 例）：Claude/Codex 各情境的套用→還原、table 區段保留、
重套冪等、引用計數（最後一個才還原）、殭屍 owner 清理、disable 旗標。
Node 端以 CLI（`node globalOverlay.js acquire|release|status`）在臨時 HOME 手動驗證互通。

## 打包

無需改 `CodexAutoAI.spec`（PyInstaller 自動跟隨 `import global_overlay`）。
`globalOverlay.js` 位於 extension 根目錄、不在 `.vscodeignore`，vsce 會自動納入。
