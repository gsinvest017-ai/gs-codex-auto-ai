<#
release-vsix.ps1 — build .vsix + 建 git tag + 發佈到 GitHub Release。
版號取自 vscode-extension/package.json（或 -Version 指定）。tag 用 ext-v<ver>（與桌面 App 的 app-v* 區隔）。
只建 tag + 上傳 .vsix 資產，不 push 主幹程式碼。
#>
[CmdletBinding()]
param([string]$Version = "", [switch]$NoPublish)
$ErrorActionPreference = "Stop"
$ext = $PSScriptRoot
$root = Split-Path -Parent $ext
Set-Location $root

if (-not $Version) {
  $Version = (Get-Content (Join-Path $ext "package.json") | ConvertFrom-Json).version
}
$Version = $Version.Trim()
$tag = "ext-v$Version"
$asset = "dist/codexautoai-$Version.vsix"

Write-Host "=== Release CodexAutoAI extension $Version ===" -ForegroundColor White
& pwsh (Join-Path $ext "build-vsix.ps1")
if (-not (Test-Path $asset)) { throw "找不到 .vsix：$asset" }

if ($NoPublish) { Write-Host "[release] -NoPublish：略過 tag/上傳。產物在 $asset" -ForegroundColor Yellow; return }

# git tag（annotated，繁中主體）
git tag -a $tag -m "VS Code extension $Version：自帶框架快照 + 自動檢查更新，裝 .vsix 即可用"
git push origin $tag

# GitHub Release（private repo 可用）
gh release create $tag $asset --title "CodexAutoAI VS Code extension $Version" --notes @"
VS Code extension（.vsix）：在 VS Code 命令面板執行「Extensions: Install from VSIX…」選此檔，或
``code --install-extension codexautoai-$Version.vsix``。

- 四個指令：初始化 / 啟動 / 設定·修復 / 檢查更新
- 啟動時自動檢查 GitHub Release 是否有新版（可在設定 codexautoai.checkForUpdates 關閉）
"@
Write-Host "[release] ✓ 已發佈 $tag" -ForegroundColor Green
