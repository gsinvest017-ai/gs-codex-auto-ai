#!/usr/bin/env bash
# build-vsix.sh — 複製框架快照進 extension，用 vsce 打包成 dist/codexautoai-<ver>.vsix。
set -euo pipefail
EXT="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$EXT")"
cd "$EXT"

FW="$EXT/framework"
rm -rf "$FW"; mkdir -p "$FW/src" "$FW/docs"
for d in .claude tools DESIGN .githooks; do [ -e "$ROOT/$d" ] && cp -r "$ROOT/$d" "$FW/"; done
for f in CLAUDE.md AGENTS.md setup.cmd setup.ps1 setup.sh .gitattributes; do [ -e "$ROOT/$f" ] && cp "$ROOT/$f" "$FW/"; done
cp -r "$ROOT/src/codexautoai_v2" "$FW/src/"
[ -d "$ROOT/docs/templates" ] && cp -r "$ROOT/docs/templates" "$FW/docs/"
find "$FW" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

cp "$ROOT/desktop/codexautoai.png" "$EXT/icon.png"

mkdir -p "$ROOT/dist"
VER="$(node -p "require('./package.json').version")"
OUT="$ROOT/dist/codexautoai-$VER.vsix"
echo "[vsix] 打包 → $OUT"
npx --yes @vscode/vsce package --no-dependencies -o "$OUT"
echo "[vsix] ✓ 完成：$OUT"
