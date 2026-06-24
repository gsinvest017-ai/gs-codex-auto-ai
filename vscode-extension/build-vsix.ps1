<#
build-vsix.ps1 — 把框架快照複製進 extension，再用 vsce 打包成 dist/codexautoai-<ver>.vsix。
框架快照（framework/）與 icon.png 為打包產物，不入庫（每次重新複製避免漂移）。
#>
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$ext = $PSScriptRoot
$root = Split-Path -Parent $ext
Set-Location $ext

# 1. 複製框架快照
$fw = Join-Path $ext "framework"
if (Test-Path $fw) { Remove-Item $fw -Recurse -Force }
New-Item -ItemType Directory -Force $fw | Out-Null
$dirs  = @(".claude", "tools", "DESIGN", ".githooks")
$files = @("CLAUDE.md", "AGENTS.md", "setup.cmd", "setup.ps1", "setup.sh", ".gitattributes")
foreach ($d in $dirs)  { if (Test-Path (Join-Path $root $d)) { Copy-Item (Join-Path $root $d) -Destination $fw -Recurse -Force } }
foreach ($f in $files) { if (Test-Path (Join-Path $root $f)) { Copy-Item (Join-Path $root $f) -Destination $fw -Force } }
# src 只帶框架引擎；docs 只帶 templates
New-Item -ItemType Directory -Force (Join-Path $fw "src") | Out-Null
Copy-Item (Join-Path $root "src/codexautoai_v2") -Destination (Join-Path $fw "src") -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $fw "docs") | Out-Null
if (Test-Path (Join-Path $root "docs/templates")) { Copy-Item (Join-Path $root "docs/templates") -Destination (Join-Path $fw "docs") -Recurse -Force }
Get-ChildItem $fw -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 2. icon（重用桌面 App 圖示）
Copy-Item (Join-Path $root "desktop/codexautoai.png") -Destination (Join-Path $ext "icon.png") -Force

# 3. 打包
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force $dist | Out-Null
$ver = (Get-Content (Join-Path $ext "package.json") | ConvertFrom-Json).version
$out = Join-Path $dist "codexautoai-$ver.vsix"
Write-Host "[vsix] 打包 → $out" -ForegroundColor Cyan
& npx --yes @vscode/vsce package --no-dependencies -o $out
if (-not (Test-Path $out)) { throw "vsce 打包失敗" }
Write-Host "[vsix] ✓ 完成：$out" -ForegroundColor Green
