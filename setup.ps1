<#
.SYNOPSIS
  CodexAutoAI 一鍵首次設定（Windows PowerShell）。
.DESCRIPTION
  一條龍完成：檢查環境 → 安裝/登入 Claude → 安裝/登入 Codex → 安裝/登入 gh → 啟用 git hooks。
  缺的 CLI 會自動智慧安裝（claude/codex 用 npm；gh 用 winget→scoop→choco 擇一），再開瀏覽器登入。
  全程冪等：已完成的步驟自動跳過，可安全重跑。
.EXAMPLE
  ./setup.ps1
  ./setup.ps1 -DryRun       # 只檢查並列出會做什麼，不實際登入/改設定
  ./setup.ps1 -ForceLogin   # 即使偵測到已登入也重新登入
  ./setup.ps1 -SkipHooks    # 不啟用 git hooks
#>
[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$ForceLogin,
  [switch]$SkipHooks
)
$ErrorActionPreference = "Stop"
# 讓 Windows PowerShell 5.1 主控台也能正確顯示繁體中文（本檔為 UTF-8 with BOM）。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
Set-Location -Path $PSScriptRoot

function Ok($m)   { Write-Host "  [OK]  $m" -ForegroundColor Green }
function Skip($m) { Write-Host "  [--]  $m（跳過）" -ForegroundColor Yellow }
function Todo($m) { Write-Host "  [>>]  $m" -ForegroundColor Cyan }
function Err($m)  { Write-Host "  [XX]  $m" -ForegroundColor Red }
function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Run([scriptblock]$b, [string]$label) {
  if ($DryRun) { Todo "[dry-run] $label" } else { & $b }
}
# 安裝後把新的 PATH 從登錄檔撈回目前 session（winget 裝完同一視窗常還找不到指令）。
function Update-EnvPath {
  $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $u = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = (@($m, $u) | Where-Object { $_ }) -join ";"
}
# 用可用的套件管理器安裝 GitHub CLI（winget → scoop → choco 擇一）。
function Install-Gh {
  if (Have winget) {
    winget install --id GitHub.cli -e --silent --accept-package-agreements --accept-source-agreements
  } elseif (Have scoop) {
    scoop install gh
  } elseif (Have choco) {
    choco install gh -y
  } else {
    throw "找不到 winget / scoop / choco，請手動安裝：https://cli.github.com"
  }
  Update-EnvPath
}

Write-Host "CodexAutoAI 一鍵設定" -ForegroundColor White -NoNewline
if ($DryRun) { Write-Host "  (dry-run)" -ForegroundColor Yellow } else { Write-Host "" }
Write-Host "──────────────────────────────────"

# --- 步驟 1：環境前置檢查 ---
Write-Host "1. 環境前置檢查"
$py = $null
foreach ($cand in @("python", "python3")) { if (Have $cand) { $py = $cand; break } }
if (-not $py) { Err "找不到 python，請先安裝 Python >= 3.11"; exit 1 }
Ok "python：$(& $py --version 2>&1)"
if (-not (Have git)) { Err "找不到 git"; exit 1 }
Ok "git：$((git --version).Split(' ')[2])"
if (Have npm) { Ok "npm：$(npm --version)" } else { Skip "npm 未安裝（若 Codex 也未安裝將無法自動安裝）" }

# --- 步驟 2：Claude Code 安裝 + 登入 ---
Write-Host "2. Claude Code 安裝 + 登入"
if (-not (Have claude)) {
  if (Have npm) { Todo "安裝 @anthropic-ai/claude-code…"; Run { npm install -g '@anthropic-ai/claude-code' } "npm install -g @anthropic-ai/claude-code"; Update-EnvPath }
  else { Err "claude 與 npm 都不存在，無法自動安裝 Claude。請先裝 Node.js 後重跑。" }
}
if ((Have claude) -or $DryRun) {
  $cred = Join-Path $HOME ".claude/.credentials.json"
  if ((-not $ForceLogin) -and (Test-Path $cred)) {
    Skip "Claude 已登入（偵測到 .credentials.json）"
  } else {
    Todo "開啟瀏覽器登入 Claude…"; Run { claude login } "claude login"
  }
}

# --- 步驟 3：OpenAI Codex CLI 安裝 + 登入 ---
Write-Host "3. OpenAI Codex CLI"
if (-not (Have codex)) {
  if (Have npm) { Todo "安裝 @openai/codex…"; Run { npm install -g '@openai/codex' } "npm install -g @openai/codex" }
  else { Err "codex 與 npm 都不存在，無法安裝 Codex。請先裝 Node.js 後重跑。" }
}
if ((Have codex) -or $DryRun) {
  $loggedIn = $false
  if (-not $ForceLogin) {
    try { codex login status *> $null; if ($LASTEXITCODE -eq 0) { $loggedIn = $true } } catch {}
  }
  if ($loggedIn) { Skip "Codex 已登入（codex login status 通過）" }
  else { Todo "開啟瀏覽器登入 Codex…"; Run { codex login } "codex login" }
}

# --- 步驟 4：GitHub CLI（gh）安裝 + 登入 ---
# 自動更新檢查（桌面 App / extension）需要 gh token 才能讀 private repo 的 Release。
Write-Host "4. GitHub CLI（gh，自動檢查更新用）"
if (-not (Have gh)) {
  if ($DryRun) { Todo "[dry-run] 安裝 GitHub CLI（winget → scoop → choco 擇一）" }
  else {
    Todo "智慧安裝 GitHub CLI…"
    try { Install-Gh } catch { Err "自動安裝 gh 失敗：$_" }
  }
}
if ((Have gh) -or $DryRun) {
  $ghAuthed = $false
  if (-not $ForceLogin) {
    try { gh auth status *> $null; if ($LASTEXITCODE -eq 0) { $ghAuthed = $true } } catch {}
  }
  if ($ghAuthed) { Skip "gh 已登入（gh auth status 通過）" }
  else { Todo "開啟瀏覽器登入 GitHub…"; Run { gh auth login --hostname github.com --git-protocol https --web } "gh auth login --web" }
} elseif (-not $DryRun) {
  Err "gh 仍不可用——請手動安裝 https://cli.github.com 後重跑（沒有 gh 也可改設環境變數 GH_TOKEN）。"
}

# --- 步驟 5：啟用 git hooks ---
Write-Host "5. 啟用 git hooks（AGENTS.md commit 時自動同步）"
if ($SkipHooks) { Skip "依 -SkipHooks 跳過" }
else { Run { & $py tools/install_hooks.py } "python tools/install_hooks.py" }

# --- 完成 ---
Write-Host "──────────────────────────────────"
if ($DryRun) {
  Write-Host "dry-run 結束：以上為實際執行時會做的動作。" -ForegroundColor Yellow
} else {
  Write-Host "設定完成！" -ForegroundColor Green
  Write-Host "下一步：在本資料夾執行  claude  ，然後打  /start  或直接描述需求。"
}
