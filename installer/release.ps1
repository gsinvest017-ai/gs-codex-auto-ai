<#
release.ps1 — build App + 安裝檔，建 git tag，發佈到 GitHub Release。
版號取自 desktop/VERSION（或 -Version 指定）。只建 tag + 上傳資產，不 push 主幹程式碼。
#>
[CmdletBinding()]
param([string]$Version = "", [switch]$NoPublish)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $Version) {
  $Version = (Get-Content "desktop/VERSION" -ErrorAction SilentlyContinue | Select-Object -First 1)
  if (-not $Version) { $Version = "0.1.0" }
}
$Version = $Version.Trim()
$tag = "app-v$Version"
$asset = "dist/CodexAutoAI-setup-$Version.exe"

Write-Host "=== Release CodexAutoAI App $Version ===" -ForegroundColor White
& pwsh (Join-Path $PSScriptRoot "build-app.ps1")
& pwsh (Join-Path $PSScriptRoot "build-installer.ps1") -Version $Version
if (-not (Test-Path $asset)) { throw "找不到安裝檔：$asset" }

if ($NoPublish) { Write-Host "[release] -NoPublish：略過 tag/上傳。產物在 $asset" -ForegroundColor Yellow; return }

# git tag（annotated，繁中主體）
git tag -a $tag -m "桌面 App $Version：點圖示即可啟用 CodexAutoAI"
git push origin $tag

# GitHub Release（發到 PUBLIC 鏡像 repo，免 token 供使用者自動更新；tag 建在鏡像 main）
gh release create $tag $asset --repo gsinvest017-ai/gs-codex-auto-ai-releases --target main --title "CodexAutoAI 桌面 App $Version" --notes @"
一鍵桌面啟動器：下載安裝檔 → 點桌面金色圖示 → 環境檢查/一鍵設定 → 輸入需求按啟動。

- 免系統管理員，裝到 %LOCALAPPDATA%\CodexAutoAI
- 仍需先安裝並登入 Claude Code 與 OpenAI Codex（App 內「設定/修復」會引導）
"@
Write-Host "[release] ✓ 已發佈 $tag" -ForegroundColor Green
