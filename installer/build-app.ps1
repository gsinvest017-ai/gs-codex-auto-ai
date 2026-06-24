<#
build-app.ps1 — 用 PyInstaller 把 desktop/launcher.py 打包成 dist/CodexAutoAI.exe。
GUI 程式（--noconsole），單檔（--onefile），帶自製圖示。
#>
[CmdletBinding()]
param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 確保圖示存在（需要 Pillow）
if (-not (Test-Path "desktop/codexautoai.ico")) {
  Write-Host "[build-app] 生成圖示…" -ForegroundColor Cyan
  & $Python -m pip install --quiet pillow
  & $Python desktop/make_icon.py
}

Write-Host "[build-app] PyInstaller 打包…" -ForegroundColor Cyan
& $Python -m PyInstaller --noconfirm --clean --noconsole --onefile `
  --icon "desktop/codexautoai.ico" `
  --name "CodexAutoAI" `
  "desktop/launcher.py"

if (-not (Test-Path "dist/CodexAutoAI.exe")) { throw "build 失敗：找不到 dist/CodexAutoAI.exe" }
Write-Host "[build-app] ✓ 完成：dist/CodexAutoAI.exe" -ForegroundColor Green
