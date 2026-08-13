<#
.SYNOPSIS
  編譯 Codex plugin 一鍵安裝執行檔（dist\CodexPlugin-setup-<ver>.exe）。
.DESCRIPTION
  只需要 Inno Setup 6，不需要 PyInstaller——這支安裝檔的酬載只有一個 .ps1。
.EXAMPLE
  pwsh installer/plugin-bootstrap/build.ps1
  pwsh installer/plugin-bootstrap/build.ps1 -Version 1.1.0
#>
[CmdletBinding()]
param([string]$Version = "")
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
Set-Location -Path $PSScriptRoot

if (-not $Version) {
  $vf = Join-Path $PSScriptRoot "VERSION"
  $Version = if (Test-Path $vf) { (Get-Content -Raw $vf).Trim() } else { "1.0.0" }
}

# ISCC 的候選位置：系統安裝與**免管理員的 per-user 安裝**都要找。
# 後者是 #71 補上的——本 App 自己就是 PrivilegesRequired=lowest，建置機沒有管理員
# 權限是常態，只找 Program Files 會在那些機器上直接失敗。
$candidates = @(
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
  throw "找不到 ISCC.exe（Inno Setup 6）。安裝：winget install JRSoftware.InnoSetup`n找過：`n  " + ($candidates -join "`n  ")
}

# 這支安裝檔真正的酬載就這一個檔案，缺了就沒有意義——先擋掉，別編出一個空殼。
$script = Join-Path $PSScriptRoot "Install-CodexPlugin.ps1"
if (-not (Test-Path $script)) { throw "找不到 $script" }

# 沒有 BOM 的話，Windows PowerShell 5.1 會把裡面的繁體中文讀成亂碼——而這支腳本
# 正是要在「只有內建 PowerShell」的機器上跑。編譯前擋下來，不要讓它流到使用者手上。
$head = [System.IO.File]::ReadAllBytes($script)[0..2]
if (-not ($head[0] -eq 0xEF -and $head[1] -eq 0xBB -and $head[2] -eq 0xBF)) {
  throw "Install-CodexPlugin.ps1 缺少 UTF-8 BOM，PowerShell 5.1 會顯示亂碼。請以 UTF-8 with BOM 存檔。"
}

Write-Host "ISCC : $iscc" -ForegroundColor DarkGray
Write-Host "版本 : $Version" -ForegroundColor DarkGray

& $iscc "/DAppVersion=$Version" "setup.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC 編譯失敗（exit $LASTEXITCODE）" }

$out = Join-Path $PSScriptRoot "..\..\dist\CodexPlugin-setup-$Version.exe"
$out = [System.IO.Path]::GetFullPath($out)
if (-not (Test-Path $out)) { throw "編譯完成但找不到輸出：$out" }
$kb = [math]::Round((Get-Item $out).Length / 1KB)
Write-Host ""
Write-Host "完成：$out（$kb KB）" -ForegroundColor Green
