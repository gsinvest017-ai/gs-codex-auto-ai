<#
build-app.ps1 — 用 PyInstaller 把 desktop/launcher.py 打包成 dist/CodexAutoAI.exe。
GUI 程式（--noconsole），單檔（--onefile），帶自製圖示。
#>
[CmdletBinding()]
param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ── 建置解譯器 ───────────────────────────────────────────────────────────────
# PyInstaller 打包的是**它自己執行環境**的套件，不是專案宣告的依賴。用錯環境就缺
# 東西，而且照樣回報成功——這條線已經踩過一次：`python` 走 PATH 指到系統 Python，
# 那裡的 keyguard 是 editable，於是把 keyguard 裝進專案 venv 完全沒用。
# 專案自己的 venv 優先，其次才是 -Python 指定 / PATH。
if ($PSBoundParameters.ContainsKey('Python')) {
  # 呼叫端明講了用哪個，就聽它的
} elseif (Test-Path ".venv\Scripts\python.exe") {
  $Python = (Resolve-Path ".venv\Scripts\python.exe").Path
} elseif (Test-Path "venv\Scripts\python.exe") {
  $Python = (Resolve-Path "venv\Scripts\python.exe").Path
} else {
  Write-Host "[build-app] WARNING: 沒有專案 venv，改用 PATH 上的 python" -ForegroundColor Yellow
}
Write-Host "[build-app] 建置解譯器：$Python" -ForegroundColor DarkGray

# ── 授權閘門 A：keyguard 必須裝在建置解譯器裡，且不能是 editable ──────────────
# KEYGUARD 的套件目錄是 `src`、import 名是 `keyguard`，editable 安裝靠 .pth ＋
# 自訂 finder 在執行期做這個對映，PyInstaller 的靜態模組圖**追不到**，會靜默漏包。
# 而 licensing.py 的 fallback 是刻意 fail-open（避免打包失誤害到付費客戶），
# 兩者相加 = 靜默出貨一個完全不強制的 release。
# 訊息一律 ASCII：zh-TW 主控台走 cp950，非 ASCII 會變成 ?? 把唯一的說明洗掉。
$kgShow = (& $Python -m pip show keyguard 2>&1 | Out-String)
if ($kgShow -notmatch "(?m)^Name:") {
  throw ("keyguard is not installed in the build interpreter:`n  $Python`n`n" +
         "Install it there (NOT editable):`n" +
         "    & '$Python' -m pip install <path-to-KEYGUARD>")
}
if ($kgShow -match "(?m)^Editable project location:") {
  throw ("keyguard is installed EDITABLE in the build interpreter, so PyInstaller`n" +
         "will omit it and the build will look completely fine while enforcing`n" +
         "nothing.`n`n  $Python`n`n" +
         "--hidden-import, --collect-submodules and --paths do NOT help.`n" +
         "Reinstall normally:`n" +
         "    & '$Python' -m pip uninstall -y keyguard`n" +
         "    & '$Python' -m pip install <path-to-KEYGUARD>")
}
Write-Host "[build-app] OK    keyguard 非 editable" -ForegroundColor DarkGray

# 確保圖示存在（需要 Pillow）
if (-not (Test-Path "desktop/codexautoai.ico")) {
  Write-Host "[build-app] 生成圖示…" -ForegroundColor Cyan
  & $Python -m pip install --quiet pillow
  & $Python desktop/make_icon.py
}

Write-Host "[build-app] PyInstaller 打包…" -ForegroundColor Cyan
# --add-data：內嵌終端機的前端資產（terminal.html + vendored xterm.js）是資料檔，
# PyInstaller 的模組分析看不到，不明寫就不會被打包，發行版按終端機按鈕只會拿到 404。
# Windows 的分隔符是 `;`。對應的讀取端見 termserver._web_root()（會找 sys._MEIPASS）。
& $Python -m PyInstaller --noconfirm --clean --noconsole --onefile `
  --icon "desktop/codexautoai.ico" `
  --name "CodexAutoAI" `
  --add-data "desktop/web;web" `
  "desktop/launcher.py"

if (-not (Test-Path "dist/CodexAutoAI.exe")) { throw "build 失敗：找不到 dist/CodexAutoAI.exe" }

# ── 授權閘門 B：出貨前功能驗證 ──────────────────────────────────────────────
# 閘門 A 只證明 keyguard 在 build 時裝對了。packagecheck 證明的是**成品真的會強制**：
# 它跑 `<exe> --machine-id` 與 `<exe> --licence-status`，確認強制模式生效、
# 無授權時是有界試用而不是一扇開著的門。
#
# 不要用「dist 目錄裡有沒有 keyguard 資料夾」判斷——純 Python 模組會被編進 exe 內的
# PYZ archive，看不到獨立目錄。只有功能測試算數。
#
# --require-console-output：用 CREATE_NEW_CONSOLE 開子行程並讀回螢幕緩衝區，確認
#   失敗的啟用在真實 console 看得見。pipe 檢查不到（pipe 是 handle，寫入會成功）。
# --require-window：確認 exe 內有 tcl/tk，否則拒絕會退化成裸 MessageBox，客戶連
#   自己的 machine id 都複製不出來。launcher 本來就用 tkinter，所以這項應該直接過。
Write-Host "[build-app] 出貨閘門：keyguard.packagecheck…" -ForegroundColor Cyan
& $Python -m keyguard.packagecheck "dist/CodexAutoAI.exe" `
  --email-env GS_CODEX_AUTO_AI_LICENCE_EMAIL `
  --require-console-output `
  --require-window
if ($LASTEXITCODE -ne 0) {
  throw ("Licence gate FAILED (exit $LASTEXITCODE).`n`n" +
         "The build exists at dist/CodexAutoAI.exe but did not pass its own gate.`n" +
         "DO NOT SHIP IT.")
}

Write-Host "[build-app] ✓ 完成：dist/CodexAutoAI.exe" -ForegroundColor Green
