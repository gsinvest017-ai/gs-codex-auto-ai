<#
build-installer.ps1 — 備妥 payload + 用 Inno Setup 6 編譯出 dist/CodexAutoAI-setup-<ver>.exe。
先確保 dist/CodexAutoAI.exe 存在（沒有就先跑 build-app.ps1）。
#>
[CmdletBinding()]
param([string]$Version = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $Version) {
  $Version = (Get-Content "desktop/VERSION" -ErrorAction SilentlyContinue | Select-Object -First 1)
  if (-not $Version) { $Version = "0.1.0" }
}
$Version = $Version.Trim()

# 1. 確保 launcher exe 已 build
if (-not (Test-Path "dist/CodexAutoAI.exe")) {
  Write-Host "[installer] 先 build launcher exe…" -ForegroundColor Cyan
  & pwsh (Join-Path $PSScriptRoot "build-app.ps1")
}

# 2. 找 Inno Setup 6
$iscc = $null
foreach ($c in @("iscc", "ISCC", "C:\Program Files (x86)\Inno Setup 6\ISCC.exe", "C:\Program Files\Inno Setup 6\ISCC.exe")) {
  $cmd = Get-Command $c -ErrorAction SilentlyContinue
  if ($cmd) { $iscc = $cmd.Source; break }
  if (Test-Path $c) { $iscc = $c; break }
}
if (-not $iscc) { throw "找不到 Inno Setup 6 (ISCC.exe)。請安裝：https://jrsoftware.org/isinfo.php" }
Write-Host "[installer] ISCC: $iscc" -ForegroundColor DarkGray

# 3. 備妥 payload（框架檔 + exe + 圖示），排除開發/產物
$payload = Join-Path $PSScriptRoot "payload"
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory -Force $payload | Out-Null

$includeDirs  = @(".claude", "tools", "src", "DESIGN", ".githooks")
$includeFiles = @("CLAUDE.md", "AGENTS.md", "README.md", "setup.cmd", "setup.ps1", "setup.sh", ".gitattributes")
foreach ($d in $includeDirs) {
  if (Test-Path $d) {
    Copy-Item $d -Destination $payload -Recurse -Force
  }
}
foreach ($f in $includeFiles) { if (Test-Path $f) { Copy-Item $f -Destination $payload -Force } }
# docs：只帶 templates（報告模板）
New-Item -ItemType Directory -Force (Join-Path $payload "docs") | Out-Null
if (Test-Path "docs/templates") { Copy-Item "docs/templates" -Destination (Join-Path $payload "docs") -Recurse -Force }
# 圖示（捷徑用）
New-Item -ItemType Directory -Force (Join-Path $payload "desktop") | Out-Null
Copy-Item "desktop/codexautoai.ico" -Destination (Join-Path $payload "desktop") -Force
# launcher exe
Copy-Item "dist/CodexAutoAI.exe" -Destination $payload -Force

# 清掉混進來的快取
Get-ChildItem $payload -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 4. 編譯
Write-Host "[installer] 編譯 setup.iss (v$Version)…" -ForegroundColor Cyan
& $iscc "/DAppVersion=$Version" (Join-Path $PSScriptRoot "setup.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC 編譯失敗 (exit $LASTEXITCODE)" }

$out = "dist/CodexAutoAI-setup-$Version.exe"
if (-not (Test-Path $out)) { throw "找不到輸出：$out" }
Write-Host "[installer] ✓ 完成：$out" -ForegroundColor Green
