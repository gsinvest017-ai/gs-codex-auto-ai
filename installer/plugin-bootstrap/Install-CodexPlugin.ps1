<#
.SYNOPSIS
  一鍵把 OpenAI 官方的 Codex plugin 裝進 Claude Code（Windows）。
.DESCRIPTION
  裝完之後在 Claude Code 裡就能用：
    /codex:review              請 Codex 審查目前的 git 變更
    /codex:adversarial-review  請 Codex 唱反調，挑戰設計取捨
    /codex:rescue              卡住時把任務交給 Codex 接手
    /codex:transfer            把整段 session 轉成可 resume 的 Codex thread

  全程冪等：已完成的步驟會直接跳過，可以安全重跑。
  本檔完全獨立，不依賴 CodexAutoAI，也不需要 Python。

  離開碼（給自動化判斷用，非 0 代表該階段沒過）：
    0  完成（plugin 可用）
    2  找不到 Claude Code，且無法自動安裝
    3  找不到 Node.js / npm
    4  marketplace 或 plugin 安裝失敗
    5  plugin 裝好了，但 Codex CLI 不可用
    6  全部裝好了，但 Codex 尚未登入（跑 `codex login` 即可）
.EXAMPLE
  ./Install-CodexPlugin.ps1
  ./Install-CodexPlugin.ps1 -DryRun    # 只說會做什麼，不動系統
  ./Install-CodexPlugin.ps1 -Json      # 只印一行 JSON，給程式讀
#>
[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$Json,
  [switch]$NoLogin,
  [switch]$Pause
)
$ErrorActionPreference = "Stop"
# 讓 Windows PowerShell 5.1 主控台也能正確顯示繁體中文（本檔為 UTF-8 with BOM）。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$MARKETPLACE = "openai/codex-plugin-cc"
$PLUGIN      = "codex@openai-codex"

# -Json 模式下所有人類可讀訊息都不能印到 stdout，否則呼叫端解析不到那一行 JSON。
function Say($m, $c)  { if (-not $Json) { Write-Host $m -ForegroundColor $c } }
function Ok($m)       { Say "  [OK]  $m" Green }
function Skip($m)     { Say "  [--]  $m（跳過）" Yellow }
function Todo($m)     { Say "  [>>]  $m" Cyan }
function Err($m)      { Say "  [XX]  $m" Red }
function Have($c)     { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }

# 安裝後把新的 PATH 從登錄檔撈回目前 session——npm -g 裝完，同一個視窗常常還找不到指令。
function Update-EnvPath {
  $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $u = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = (@($m, $u) | Where-Object { $_ }) -join ";"
}

$result = [ordered]@{
  ok = $false; stage = "start"; exitCode = 0
  claude = $false; node = $false; npm = $false
  marketplace = $false; plugin = $false
  codex = $false; loggedIn = $null      # 三態：$true / $false / $null（問不到）
  pluginPath = ""; message = ""
}

function Finish([int]$code, [string]$stage, [string]$msg) {
  $result.exitCode = $code
  $result.stage    = $stage
  $result.message  = $msg
  $result.ok       = ($code -eq 0)
  if ($Json) {
    # ConvertTo-Json 在 PS 5.1 會把單行輸出斷行，-Compress 才保證是一行。
    Write-Output ($result | ConvertTo-Json -Compress -Depth 4)
  } else {
    Say "" White
    if ($code -eq 0) {
      Say "完成。在 Claude Code 裡輸入 /codex: 就會看到四個新指令。" Green
    } else {
      Err $msg
    }
  }
  # 從安裝檔啟動時這個主控台是安裝程式生出來的，腳本一結束就消失——使用者連
  # 成功還失敗都來不及看。-Pause 讓它等一下人。
  # 有上限，而且沒有互動主控台時直接跳過——不然 /VERYSILENT 的無人值守安裝
  # 會永遠卡在等一個不存在的人按鍵。
  if ($Pause -and -not $Json -and [Environment]::UserInteractive) {
    Say "" White
    Say "按任意鍵關閉（60 秒後自動關閉）…" DarkGray
    $deadline = (Get-Date).AddSeconds(60)
    try {
      while ((Get-Date) -lt $deadline) {
        if ($Host.UI.RawUI.KeyAvailable) {
          $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown"); break
        }
        Start-Sleep -Milliseconds 200
      }
    } catch { Start-Sleep -Seconds 5 }   # 沒有真的主控台（被導向、被包起來跑）
  }
  exit $code
}

Say "" White
Say "Codex plugin for Claude Code — 安裝程式" Cyan
Say "----------------------------------------" DarkGray

# ── 1. Claude Code ─────────────────────────────────────────────────────────
# plugin 是「裝進 Claude Code 裡」的東西，沒有它整件事不成立，所以這關最硬。
if (Have claude) {
  $result.claude = $true
  Ok "Claude Code 已安裝"
} elseif (Have npm) {
  Todo "找不到 Claude Code，改用 npm 安裝…"
  if (-not $DryRun) {
    try { npm install -g '@anthropic-ai/claude-code' 2>&1 | Out-Null; Update-EnvPath } catch {}
  }
  if ($DryRun -or (Have claude)) { $result.claude = $true; Ok "Claude Code 安裝完成" }
  else { Finish 2 "claude" "Claude Code 安裝失敗，請手動安裝後重跑：npm install -g @anthropic-ai/claude-code" }
} else {
  Finish 2 "claude" "找不到 Claude Code，也沒有 npm 可以裝它。請先安裝 Node.js（https://nodejs.org）再重跑。"
}

# ── 2. Node / npm ──────────────────────────────────────────────────────────
# plugin 的執行期是 .mjs 腳本，Codex CLI 也是 npm 套件，兩者都要 Node。
$result.node = (Have node)
$result.npm  = (Have npm)
if ($result.node) { Ok "Node.js 已安裝" } else { Err "找不到 Node.js" }
if ($result.npm)  { Ok "npm 已安裝" }      else { Err "找不到 npm" }
if (-not ($result.node -and $result.npm)) {
  Finish 3 "node" "Codex plugin 需要 Node.js 與 npm。請安裝 Node.js LTS（https://nodejs.org）後重跑。"
}

# ── 3. 加入 marketplace 並安裝 plugin ──────────────────────────────────────
# 兩個指令都是冪等的（已存在時回 rc=0 並說 already），所以重跑安全。
Todo "加入 marketplace $MARKETPLACE…"
if ($DryRun) {
  $result.marketplace = $true
} else {
  $out = & claude plugin marketplace add $MARKETPLACE 2>&1
  if ($LASTEXITCODE -eq 0) { $result.marketplace = $true; Ok "marketplace 就緒" }
  else { Finish 4 "marketplace" "加入 marketplace 失敗：$out" }
}

Todo "安裝 plugin $PLUGIN…"
if ($DryRun) {
  $result.plugin = $true
} else {
  # -y 是必要的：stdout 不是 TTY 時（被程式呼叫、或導向檔案）沒有它會卡在確認提示。
  $out = & claude plugin install $PLUGIN -y 2>&1
  if ($LASTEXITCODE -eq 0) { $result.plugin = $true; Ok "plugin 已安裝" }
  else { Finish 4 "plugin" "安裝 plugin 失敗：$out" }
}

# ── 4. Codex CLI ───────────────────────────────────────────────────────────
if (Have codex) {
  $result.codex = $true
  Ok "Codex CLI 已安裝"
} else {
  Todo "安裝 Codex CLI（npm install -g @openai/codex）…"
  if (-not $DryRun) {
    try { npm install -g '@openai/codex' 2>&1 | Out-Null; Update-EnvPath } catch {}
  }
  if ($DryRun -or (Have codex)) { $result.codex = $true; Ok "Codex CLI 安裝完成" }
  else { Finish 5 "codex" "Codex CLI 安裝失敗。plugin 已裝好，補跑 npm install -g @openai/codex 即可。" }
}

# ── 5. 驗證：讓 plugin 自己回報狀態 ────────────────────────────────────────
# 不要自己推測「應該可以了」——plugin 有 `setup --json` 的機器可讀契約，直接問它。
# 安裝路徑帶版號（…/codex/1.0.6），寫死會在下次改版失效，所以從 installed_plugins.json 反查。
# 問 plugin 自己「Codex 登入了沒」，回 $true / $false / $null（問不到）。
#
# **不能天真地 `| ConvertFrom-Json`。** 兩個在 Windows PowerShell 5.1 才會現形的坑
# （而 5.1 正是安裝檔實際用的那一個，pwsh 7 上完全看不到）：
#   1. node 會往 stderr 印 DEP0190 DeprecationWarning。在 `$ErrorActionPreference =
#      "Stop"` 之下，原生指令的 stderr 會變成終止性錯誤，整段被 catch 掉。
#   2. 就算不終止，那行警告也會混進輸出，讓 ConvertFrom-Json 解析失敗。
# 兩者的結果一樣：明明已經登入卻回報「尚未登入」——實測 pwsh 7 說 true、5.1 說 false。
# 所以要暫時放行 stderr，再從輸出裡把 JSON 那段切出來。
function Get-CodexLoggedIn([string]$companion) {
  if (-not (Test-Path $companion)) { return $null }
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $raw = (& node $companion setup --json 2>&1 | Out-String)
  } catch {
    return $null
  } finally {
    $ErrorActionPreference = $prev
  }
  $s = $raw.IndexOf("{"); $e = $raw.LastIndexOf("}")
  if ($s -lt 0 -or $e -le $s) { return $null }
  try {
    $st = $raw.Substring($s, $e - $s + 1) | ConvertFrom-Json
    return [bool]$st.auth.loggedIn
  } catch { return $null }
}

function Get-PluginPath {
  $state = Join-Path $env:USERPROFILE ".claude\plugins\installed_plugins.json"
  if (-not (Test-Path $state)) { return "" }
  try {
    $j = Get-Content -Raw -Encoding UTF8 $state | ConvertFrom-Json
    $e = $j.plugins.$PLUGIN
    if ($e) { return ($e | Select-Object -First 1).installPath }
  } catch { return "" }
  return ""
}

if ($DryRun) { Finish 0 "dry-run" "dry-run 結束，未實際變更系統。" }

$path = Get-PluginPath
$result.pluginPath = $path
if (-not $path -or -not (Test-Path $path)) {
  Finish 4 "verify" "plugin 安裝後找不到它的檔案，請重跑一次；持續失敗請回報。"
}

$companion = Join-Path $path "scripts\codex-companion.mjs"
# **三態**：$true / $false / $null（問不到）。把「問不到」併進「沒登入」的話，驗證
# 一壞掉就會叫使用者去跑一個他其實不需要跑的 codex login——而且訊息還是斷定句。
$result.loggedIn = Get-CodexLoggedIn $companion
if ($null -eq $result.loggedIn) {
  # 驗證本身壞掉不該讓整個安裝算失敗——東西都裝好了，只是問不到狀態。
  Skip "無法取得 Codex 登入狀態（不影響安裝）"
}

# ── 6. 登入 ────────────────────────────────────────────────────────────────
# 登入一定要開瀏覽器、要人動手，沒辦法無人值守，所以只帶到門口。
if ($result.loggedIn) {
  Ok "Codex 已登入"
  Finish 0 "done" ""
}

if ($NoLogin -or $Json) {
  if ($null -eq $result.loggedIn) {
    Finish 6 "login" "全部裝好了，但**無法確認** Codex 登入狀態。若 /codex: 指令不能用，請執行 codex login。"
  }
  Finish 6 "login" "全部裝好了，但 Codex 尚未登入。請執行：codex login"
}

Say "" White
Todo "Codex 尚未登入，現在開瀏覽器登入…"
try { & codex login } catch { Err "登入指令執行失敗：$_" }

$path = Get-PluginPath
$li = Get-CodexLoggedIn (Join-Path $path "scripts\codex-companion.mjs")
if ($null -ne $li) { $result.loggedIn = $li }

if ($result.loggedIn) { Ok "Codex 已登入"; Finish 0 "done" "" }
Finish 6 "login" "尚未偵測到登入。稍後手動執行 `codex login` 即可，plugin 本身已裝好。"
