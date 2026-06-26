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

按 <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> 打開指令面板，在輸入框打 `CodexAutoAI` 就會列出四個指令。

### ① 初始化 — 把框架放進目前開啟的資料夾（自帶快照，不必先 clone）
![Step 1 初始化](https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/step1-palette.svg?raw=true)

### ② 設定 / 修復 — 自動裝 + 登入 Claude / Codex / gh，啟用 hooks（可安全重跑）
![Step 2 設定/修復](https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/step2-setup.svg?raw=true)

### ③ 啟動 — 在彈出的輸入框直接打白話需求 → 選「一般 / 非停」
![Step 3 啟動](https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/step3-launch.svg?raw=true)

### ④ 檢查更新 — 比對 GitHub Release 最新 `ext-v*` 版本
![Step 4 檢查更新](https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/step4-update.svg?raw=true)

## <img src="https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/icons/settings.svg?raw=true" width="22" align="top"> 前提

仍需登入 **Claude Code CLI** 與 **OpenAI Codex CLI**（步驟 ②「設定 / 修復」會自動引導，登入會開瀏覽器）。

## <img src="https://github.com/gsinvest017-ai/gs-codex-auto-ai/blob/main/docs/guide/icons/shield-check.svg?raw=true" width="22" align="top"> 非停模式

框架預設 `bypassPermissions`（一般工具不問權限）；選「非停（autopilot）」連回合都不停，
一路跑到交付。`commit` / `push` / 刪除等不可逆操作仍會停下來問你。
