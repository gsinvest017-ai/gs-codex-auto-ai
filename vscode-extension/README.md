<p align="center">
  <img src="https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/desktop/codexautoai.png?raw=true" alt="CodexAutoAI" width="120">
</p>

<h1 align="center">CodexAutoAI — VS Code Extension</h1>

<p align="center">
一句話描述需求，<b>Claude 當調度中心、OpenAI Codex 當寫手</b>，<br>
自動跑完 需求 → 架構 → 審查 → 寫碼 → 測試 → 交付 七個階段。
</p>

## <img src="https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/icons/package.svg?raw=true" width="22" align="top"> 安裝

下載 [Releases](https://github.com/gsinvest017-ai/gs-codex-auto-ai/releases) 的 `codexautoai-x.y.z.vsix`，然後：

```
code --install-extension codexautoai-x.y.z.vsix
```

或在 VS Code：擴充功能面板 → `…` → **Install from VSIX**。

## <img src="https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/icons/rocket.svg?raw=true" width="22" align="top"> 用法（指令面板 <kbd>Ctrl</kbd>/<kbd>Cmd</kbd> + <kbd>Shift</kbd> + <kbd>P</kbd>）

按 <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> 打開指令面板，在輸入框打 `CodexAutoAI` 即可找到任務、路由與日誌等指令。

### ⓪ 開啟控制台（免終端機 GUI）— 非開發者建議只用這個
不想碰 CLI/TUI？執行「**CodexAutoAI: 開啟控制台**」：在 VS Code 內嵌面板輸入需求 → 按
「🚀 啟動新任務（先產規格）」→ terminal **隱藏在背景跑**（預設非停模式全程不問）。
面板即時顯示：七階段進度條、**分工證據**（Claude 規劃呼叫/tokens vs Codex 實作呼叫——
進入 phase5 卻沒有 Codex 呼叫會亮紅色警示，確保不是只在 Claude 燒 token）、累計成本與迭代。
「顯示背景終端機」按鈕是除錯逃生口。

### ① 安裝設定（一鍵）— 初始化 + 登入修復合一，可安全重跑
把框架放進目前開啟的資料夾（自帶快照，不必先 clone），並自動裝 + 登入 Claude / Codex / gh、
啟用 hooks；環境都就緒時直接回報、不開終端機。
![Step 2 設定/修復](https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/step2-setup.svg?raw=true)

### ② 啟動新任務 — 在彈出的輸入框直接打白話需求 → 選「一般 / 非停」
![Step 3 啟動](https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/step3-launch.svg?raw=true)

### ③ 啟動新任務：從 spec 開始 — 先用 [gs-spec-forge](https://github.com/gsinvest017-ai/gs-spec-forge) 產規格再跑七階段
輸入意圖 → 選「一般／非停」→ 產出 spec.md → 啟動七階段開發。「啟動新任務」與此指令共用這個流程。

先嘗試設定 `codexautoai.specForgeCmd` 指定的工具、已安裝的 gs-spec-forge，以及建置時可選的快照；這些候選不可用時，最後使用內建純 Python 離線產生器建立規格草稿，再由需求階段細化。離線模式不含 gs-rag 檢索或來源引用，只需要 Python，不需要私人 repo 權限。若已安裝完整版 gs-spec-forge，可繼續使用其完整功能。

### ④ 即時預覽網頁 UI（內嵌）— pipeline 產出有前端的專案一鍵看結果
執行「CodexAutoAI: 即時預覽網頁 UI」（或控制台的 🌐 按鈕），自動降級、全在 VS Code 內嵌：
1. 有裝 [Live Preview](https://marketplace.visualstudio.com/items?itemName=ms-vscode.live-server)（「安裝設定」會自動幫你裝）→ 用它（hot reload 最佳體驗）
2. 沒裝 → 自動起本機 static server（`python -m http.server`）+ VS Code 內建 Simple Browser（零安裝）
3. server 型專案（Flask/FastAPI/Node…）→ **偵測埠號**；server 已在跑直接預覽、沒跑則
   **一鍵背景啟動**（自動找 `run.ps1` / `npm run dev` / `app.py` 等入口）等埠開了再開內嵌預覽

### ⑤ 檢查更新 — 比對 GitHub Release 最新 `ext-v*` 版本
![Step 4 檢查更新](https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/step4-update.svg?raw=true)

### ⑥ 中止（下一個回合邊界停止）— 停止正在跑的 pipeline
執行「CodexAutoAI: 中止（下一個回合邊界停止）」會寫入 `log/abort.flag`，讓 `tools/autopilot/cont.py`
在**下一個回合邊界**停止 autopilot 續跑；**無法立即中斷正在執行的 Codex 子行程**。
也可從控制台面板標題列的停止圖示觸發。

## <img src="https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/icons/settings.svg?raw=true" width="22" align="top"> 前提

仍需登入 **Claude Code CLI** 與 **OpenAI Codex CLI**（步驟 ②「設定 / 修復」會自動引導，登入會開瀏覽器）。

## <img src="https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/icons/shield-check.svg?raw=true" width="22" align="top"> 非停模式

框架預設 `bypassPermissions`（一般工具不問權限）；選「非停（autopilot）」連回合都不停，
一路跑到交付。`commit` / `push` / 刪除等不可逆操作仍會停下來問你。


### 0.13.0 任務入口與智慧路由

啟動新任務會先產生規格，再啟動七階段流程，與桌面版的主入口一致。控制台可預覽 Python 共用 router 的 scenario、供應商、模型與原因，並套用「Codex 優先」或「多供應商」模式；設定只寫入目前專案的 `log/model-routing.json`，既有設定會備份。多供應商模式需要各 CLI 已安裝並登入；3D modeling 預設使用 Codex `gpt-6-astra`，是否能執行仍取決於該帳號模型權限。

啟動時會寫入 `log/app-run.json` 並維護心跳；控制台的「開啟任務日誌」提供生命週期與路由紀錄。原始需求透過該 terminal 的環境變數交给 runner，spec 路徑不會取代任務分類上下文。關閉 terminal、extension 或 shell integration 回報 Claude 命令結束時停止心跳；未啟用 shell integration 時，由每次任務的唯一退出標記收尾，CLI 非零退出會顯示 failed；本次 Phase 7 成功事件也可停止心跳。

本版沿用 VS Code 原生終端機與既有分工圖表，沒有移植桌面 ConPTY/xterm 多分頁；圖表的 token 統計仍以 Claude/Codex 為主，不代表其他供應商的完整用量。插件與桌面使用獨立版本號。
