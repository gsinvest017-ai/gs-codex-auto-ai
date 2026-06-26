#!/usr/bin/env bash
# setup.sh — CodexAutoAI 一鍵首次設定（Git Bash / Linux / macOS）
#
# 一條龍完成：檢查環境 → 登入 Claude → 登入 Codex → 啟用 git hooks。
# 全程冪等：已完成的步驟自動跳過，可安全重跑。
#
# 用法：
#   ./setup.sh              # 執行完整設定
#   ./setup.sh --dry-run    # 只檢查並列出會做什麼，不實際登入/改設定
#   ./setup.sh --force-login # 即使偵測到已登入也重新登入
#   ./setup.sh --skip-hooks  # 不啟用 git hooks
set -uo pipefail
cd "$(dirname "$0")"

DRY_RUN=0; FORCE_LOGIN=0; SKIP_HOOKS=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force-login) FORCE_LOGIN=1 ;;
    --skip-hooks) SKIP_HOOKS=1 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知參數：$arg" >&2; exit 2 ;;
  esac
done

# --- 小工具 ----------------------------------------------------------------
c_reset=$'\033[0m'; c_g=$'\033[32m'; c_y=$'\033[33m'; c_r=$'\033[31m'; c_b=$'\033[1m'
ok()   { printf "  ${c_g}✓${c_reset} %s\n" "$1"; }
skip() { printf "  ${c_y}↷${c_reset} %s（跳過）\n" "$1"; }
todo() { printf "  ${c_b}→${c_reset} %s\n" "$1"; }
err()  { printf "  ${c_r}✗${c_reset} %s\n" "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
run()  { if [ "$DRY_RUN" = 1 ]; then todo "[dry-run] $*"; else "$@"; fi; }
# 用可用的套件管理器安裝 GitHub CLI（依平台擇一），成功回 0。
install_gh() {
  if   have winget;  then winget install --id GitHub.cli -e --silent --accept-package-agreements --accept-source-agreements
  elif have brew;    then brew install gh
  elif have apt-get; then sudo apt-get update && sudo apt-get install -y gh
  elif have dnf;     then sudo dnf install -y gh
  elif have pacman;  then sudo pacman -S --noconfirm github-cli
  elif have scoop;   then scoop install gh
  elif have choco;   then choco install gh -y
  else return 1; fi
}

printf "${c_b}CodexAutoAI 一鍵設定${c_reset}"; [ "$DRY_RUN" = 1 ] && printf "  ${c_y}(dry-run)${c_reset}"; echo
echo "──────────────────────────────────"

# --- 步驟 1：環境前置檢查 --------------------------------------------------
echo "1. 環境前置檢查"
PY=""
for cand in python python3; do have "$cand" && { PY="$cand"; break; }; done
if [ -z "$PY" ]; then err "找不到 python，請先安裝 Python ≥ 3.11"; exit 1; fi
ok "python：$($PY --version 2>&1)"
have git || { err "找不到 git"; exit 1; }; ok "git：$(git --version | awk '{print $3}')"
if have npm; then ok "npm：$(npm --version)"; else skip "npm 未安裝（若 Codex 也未安裝將無法自動安裝）"; fi

# --- 步驟 2：Claude Code 安裝 + 登入 ---------------------------------------
echo "2. Claude Code 安裝 + 登入"
if ! have claude; then
  if have npm; then
    todo "安裝 @anthropic-ai/claude-code…"; run npm install -g @anthropic-ai/claude-code
  else
    err "claude 與 npm 都不存在，無法自動安裝 Claude。請先裝 Node.js 後重跑。"
  fi
fi
if have claude || [ "$DRY_RUN" = 1 ]; then
  CRED="$HOME/.claude/.credentials.json"
  if [ "$FORCE_LOGIN" = 0 ] && [ -f "$CRED" ]; then
    skip "Claude 已登入（偵測到 .credentials.json）"
  else
    todo "開啟瀏覽器登入 Claude…"; run claude login
  fi
fi

# --- 步驟 3：Codex CLI 安裝 + 登入 -----------------------------------------
echo "3. OpenAI Codex CLI"
if ! have codex; then
  if have npm; then
    todo "安裝 @openai/codex…"; run npm install -g @openai/codex
  else
    err "codex 與 npm 都不存在，無法安裝 Codex。請先裝 Node.js 後重跑。"
  fi
fi
if have codex || [ "$DRY_RUN" = 1 ]; then
  if [ "$FORCE_LOGIN" = 0 ] && codex login status >/dev/null 2>&1; then
    skip "Codex 已登入（codex login status 通過）"
  else
    todo "開啟瀏覽器登入 Codex…"; run codex login
  fi
fi

# --- 步驟 4：GitHub CLI（gh）安裝 + 登入 -----------------------------------
# 自動更新檢查（桌面 App / extension）需要 gh token 才能讀 private repo 的 Release。
echo "4. GitHub CLI（gh，自動檢查更新用）"
if ! have gh; then
  if [ "$DRY_RUN" = 1 ]; then
    todo "[dry-run] 安裝 GitHub CLI（winget/brew/apt/dnf/scoop/choco 擇一）"
  else
    todo "智慧安裝 GitHub CLI…"
    install_gh || err "自動安裝 gh 失敗，請手動安裝 https://cli.github.com 後重跑（或改設 GH_TOKEN）。"
  fi
fi
if have gh || [ "$DRY_RUN" = 1 ]; then
  if [ "$FORCE_LOGIN" = 0 ] && gh auth status >/dev/null 2>&1; then
    skip "gh 已登入（gh auth status 通過）"
  else
    todo "開啟瀏覽器登入 GitHub…"; run gh auth login --hostname github.com --git-protocol https --web
  fi
fi

# --- 步驟 5：啟用 git hooks ------------------------------------------------
echo "5. 啟用 git hooks（AGENTS.md commit 時自動同步）"
if [ "$SKIP_HOOKS" = 1 ]; then
  skip "依 --skip-hooks 跳過"
else
  run "$PY" tools/install_hooks.py
fi

# --- 完成 ------------------------------------------------------------------
echo "──────────────────────────────────"
if [ "$DRY_RUN" = 1 ]; then
  printf "${c_y}dry-run 結束${c_reset}：以上為實際執行時會做的動作。\n"
else
  printf "${c_g}${c_b}設定完成！${c_reset}\n"
  echo "下一步：在本資料夾執行  claude  ，然後打  /start  或直接描述需求。"
fi
