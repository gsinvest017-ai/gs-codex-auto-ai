# Codex plugin for Claude Code — 一鍵安裝

把 OpenAI 官方的 [codex-plugin-cc](https://github.com/openai/codex-plugin-cc) 設定進同事既有的 Claude Code。

裝好之後，在 Claude Code 裡多四個指令：

| 指令 | 做什麼 |
|---|---|
| `/codex:review` | 請 Codex 審查目前的 git 變更 |
| `/codex:adversarial-review` | 請 Codex 唱反調，挑戰實作方式與設計取捨 |
| `/codex:rescue` | 卡住時把任務交給 Codex 接手 |
| `/codex:transfer` | 把整段 session 轉成可 resume 的 Codex thread |

---

## 給同事（三種發法，挑一種）

**A. 執行檔**（不熟指令的人用這個）
發 `dist\CodexPlugin-setup-<ver>.exe`，點兩下，跟著走完。

> 未簽章，Windows 會跳 SmartScreen 的「已保護您的電腦」。要按「其他資訊 → 仍要執行」。
> 內部發佈時**請先講**這一步，否則多數人會在這裡停下來——這是這個發法唯一的真實摩擦。

**B. 一行指令**（工程師用這個，摩擦最低）
對方已經有 Claude Code，就一定有終端機：

```powershell
claude plugin marketplace add openai/codex-plugin-cc
claude plugin install codex@openai-codex -y
```

兩行都是冪等的，重跑安全。**這條路沒有 SmartScreen 問題**，對工程師來說比執行檔快。

**C. 腳本**
發 `Install-CodexPlugin.ps1`，讓對方跑：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-CodexPlugin.ps1
```

比 B 多做的事：缺 Claude Code / Node / Codex CLI 時會自動補裝，最後還會驗證並帶去登入。

---

## 前置需求

| 需要 | 沒有的話 |
|---|---|
| Claude Code | 腳本會用 npm 幫忙裝；連 npm 都沒有就只能請對方先裝 Node.js |
| Node.js + npm | **必須**先自己裝（https://nodejs.org）——plugin 的執行期是 `.mjs` |
| Codex CLI | 腳本會自動 `npm install -g @openai/codex` |
| Codex 登入 | 要開瀏覽器、要人動手，**無法無人值守**。腳本會帶你到那一步 |

---

## 給自動化呼叫（CodexAutoAI 的安裝流程用）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File Install-CodexPlugin.ps1 -Json
```

stdout **只會有一行 JSON**（人類訊息在 `-Json` 模式全部靜音）：

```json
{"ok":true,"stage":"done","exitCode":0,"claude":true,"node":true,"npm":true,
 "marketplace":true,"plugin":true,"codex":true,"loggedIn":true,
 "pluginPath":"C:\\Users\\...\\codex\\1.0.6","message":""}
```

離開碼就是 fallback 的判斷依據：

| 碼 | 意思 | 呼叫端該怎麼辦 |
|---|---|---|
| 0 | plugin 可用 | 走有 plugin 的路徑 |
| 2 | 找不到 Claude Code 且裝不起來 | 退回沒有 plugin 的版本 |
| 3 | 沒有 Node / npm | 退回沒有 plugin 的版本 |
| 4 | marketplace / plugin 安裝失敗 | 退回沒有 plugin 的版本 |
| 5 | plugin 裝好但 Codex CLI 不可用 | 退回沒有 plugin 的版本 |
| 6 | 都裝好了，只差登入 | 可提示使用者跑 `codex login`，功能暫不可用 |

`-Json` 隱含 `-NoLogin`（不會擅自開瀏覽器打斷無人值守流程）。

---

## 建置

```powershell
pwsh installer/plugin-bootstrap/build.ps1              # 讀 VERSION
pwsh installer/plugin-bootstrap/build.ps1 -Version 1.1.0
```

需要 Inno Setup 6（`winget install JRSoftware.InnoSetup`）。輸出在 `dist\`。

`build.ps1` 會在編譯前擋下**沒有 UTF-8 BOM** 的腳本——少了 BOM，Windows PowerShell 5.1
會把裡面的繁體中文讀成亂碼，而那正是目標機器的預設 shell。

---

## 移除

這支安裝檔**不會**在「應用程式與功能」留下項目（它沒有裝任何檔案到硬碟，只是設定
Claude Code）。要移除請用：

```powershell
claude plugin uninstall codex@openai-codex
```

---

## 已驗證

- `.exe` 以 `/VERYSILENT` 無人值守執行：先移除 plugin → 跑安裝檔 → plugin 回到
  `enabled`（`claude plugin list` 確認），全程約 2 分鐘
- 冪等：marketplace add 與 plugin install 重跑都回 `rc=0`（顯示 already）
- 失敗路徑：無 Claude Code 且無 npm → `rc=2`；有 Claude Code 但無 Node → `rc=3`

靜態守衛見 `tests/test_plugin_bootstrap.py`（BOM、Inno `[Run]` 續行、
`postinstall`+`deleteafterinstall` 衝突、離開碼契約）。
