# CodexAutoAI — VS Code Extension

一句話描述需求，**Claude 當調度中心、OpenAI Codex 當寫手**，自動跑完
需求 → 架構 → 審查 → 寫碼 → 測試 → 交付七個階段。

## 安裝

下載 [Releases](https://github.com/gsinvest017-ai/gs-codex-auto-ai/releases) 的 `codexautoai-x.y.z.vsix`，然後：

```
code --install-extension codexautoai-x.y.z.vsix
```

或在 VS Code：擴充功能面板 → `…` → Install from VSIX。

## 用法（指令面板 Ctrl/Cmd+Shift+P）

1. **CodexAutoAI: 初始化** — 把框架放進目前開啟的資料夾（已自帶快照，不必先 clone）。
2. **CodexAutoAI: 設定 / 修復** — 開終端機跑 `setup`（登入 Claude / Codex、啟用 hooks）。
3. **CodexAutoAI: 啟動** — 輸入需求 → 選「一般 / 非停」→ 開終端機跑 `claude`，pipeline 開始。

## 前提

仍需先安裝並登入 **Claude Code CLI** 與 **OpenAI Codex CLI**（「設定 / 修復」會引導，登入會開瀏覽器）。

## 非停模式

框架預設 `bypassPermissions`（一般工具不問權限）；選「非停（autopilot）」連回合都不停，
一路跑到交付。`commit` / `push` / 刪除等不可逆操作仍會停下來問你。
