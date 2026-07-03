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

# 2. 內建 gs-spec-forge 快照 —— stdlib-first 核心（vault/spec/cli，MIT），讓「啟動新任務：
#    從 spec 開始」對沒裝 gh / 沒 private repo 權限 / 沒裝 gs-spec-forge 的使用者開箱即用
#    （extension 以 `python -m gs_spec_forge.cli` 執行，唯一前置是 Python）。
#    來源：sibling clone；建置機沒有時警告並跳過（該 fallback 不可用，其餘功能不受影響）。
$sfSrc = Join-Path (Split-Path -Parent $root) "gs-spec-forge/src/gs_spec_forge"
$sfSnap = Join-Path $ext "spec_forge_snapshot"
if (Test-Path $sfSnap) { Remove-Item $sfSnap -Recurse -Force }
if (Test-Path $sfSrc) {
  New-Item -ItemType Directory -Force (Join-Path $sfSnap "gs_spec_forge") | Out-Null
  Copy-Item (Join-Path $sfSrc "*") -Destination (Join-Path $sfSnap "gs_spec_forge") -Recurse -Force
  Get-ChildItem $sfSnap -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host "[vsix] 已內建 gs-spec-forge 快照（bundled fallback）" -ForegroundColor Cyan
} else {
  Write-Host "[vsix] 警告：找不到 sibling gs-spec-forge，跳過內建快照——「從 spec 開始」的開箱 fallback 將不可用" -ForegroundColor Yellow
}

# 3. icon（重用桌面 App 圖示）
Copy-Item (Join-Path $root "desktop/codexautoai.png") -Destination (Join-Path $ext "icon.png") -Force

# 4. 打包
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force $dist | Out-Null
$ver = (Get-Content (Join-Path $ext "package.json") | ConvertFrom-Json).version
$out = Join-Path $dist "codexautoai-$ver.vsix"
Write-Host "[vsix] 打包 → $out" -ForegroundColor Cyan

# vsce 禁止 README.md 內嵌 SVG（安全限制）。GitHub 版 README 用 SVG 教學圖沒問題，
# 但打包進 .vsix 前暫時換成「去 SVG 圖」版（VS Code 擴充頁本來也不渲染這些 SVG），
# 打包完還原，repo 內的 README 一字不動。
$readme = Join-Path $ext "README.md"
$readmeBak = $null
if (Test-Path $readme) {
  $readmeBak = "$readme.release-bak"
  Copy-Item $readme $readmeBak -Force
  $clean = foreach ($ln in (Get-Content $readme)) {
    if ($ln -match '^\s*!\[[^\]]*\]\([^)]*\.svg[^)]*\)\s*$') { continue }  # 整行 SVG 圖 → 丟掉
    ($ln -replace '<img[^>]*\.svg[^>]*>\s*', '')                          # 行內 SVG <img> → 拿掉
  }
  Set-Content $readme -Value $clean -Encoding UTF8
}
try {
  & npx --yes @vscode/vsce package --no-dependencies -o $out
} finally {
  if ($readmeBak) { Move-Item $readmeBak $readme -Force }  # 一定還原（含打包失敗時）
}
if (-not (Test-Path $out)) { throw "vsce 打包失敗" }
Write-Host "[vsix] ✓ 完成：$out" -ForegroundColor Green
