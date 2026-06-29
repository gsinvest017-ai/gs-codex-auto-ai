// globalOverlay.js — 啟動套用 / 關閉還原全域 Claude/Codex 設定（Node 版，與 desktop/global_overlay.py 同協定）。
//
// 與桌面 App 共用同一個 state 檔（~/.codexautoai/overlay-state.json）、同樣的 overlay 內容與
// owner 引用計數，所以 desktop 與 extension 同時開時不會互相覆蓋：第一個 acquire 才套用、
// 最後一個 release 才還原。owner 綁 PID，下次啟動會清掉 crash 殘留的殭屍 owner。
//
// 純 Node 內建模組（fs / os / path）。所有函式不 throw——失敗只 console.warn，不擋啟動/關閉。
const fs = require("fs");
const os = require("os");
const path = require("path");

const STATE_VERSION = 1;
const OWNER_TTL_SEC = 24 * 60 * 60;

const CLAUDE_PERMISSIONS = {
  defaultMode: "bypassPermissions",
  allow: [
    "Bash(*)", "Read(*)", "Edit(*)", "Write(*)", "Glob(*)", "Grep(*)",
    "Agent(*)", "TodoWrite(*)", "WebFetch(*)", "WebSearch(*)", "Skill(*)",
  ],
  ask: [
    "Bash(git commit:*)", "Bash(git push:*)", "Bash(git reset --hard:*)",
    "Bash(git clean:*)", "Bash(rm -rf:*)", "Bash(*deploy*)",
  ],
  deny: [
    "Bash(rm -rf /*)", "Bash(rm -rf ~*)", "Bash(* | sh)",
    "Bash(curl * | bash)", "Bash(wget * | bash)", "Bash(mkfs*)", "Bash(dd if=*)",
  ],
};

const CODEX_KEYS = { approval_policy: "on-failure", sandbox_mode: "workspace-write" };
const CODEX_BEGIN = "# >>> codexautoai overlay (auto, do not edit) >>>";
const CODEX_END = "# <<< codexautoai overlay <<<";
const CODEX_KEY_RE = new RegExp(
  "^\\s*(" + Object.keys(CODEX_KEYS).join("|") + ")\\s*=", "i");

// ── 路徑 ─────────────────────────────────────────────────────────────────────
function statePath() { return path.join(os.homedir(), ".codexautoai", "overlay-state.json"); }
function claudeSettingsPath() { return path.join(os.homedir(), ".claude", "settings.json"); }
function codexConfigPath() { return path.join(os.homedir(), ".codex", "config.toml"); }

// ── PID 存活（判不準時保守回 true）──────────────────────────────────────────
function pidAlive(pid) {
  if (!pid || pid <= 0) return false;
  try {
    process.kill(pid, 0); // 不送訊號、只探活
    return true;
  } catch (e) {
    return e && e.code === "EPERM"; // 存在但無權 → 視為存活
  }
}

// ── state 讀寫 ───────────────────────────────────────────────────────────────
function emptyState() { return { version: STATE_VERSION, owners: {}, backup: null }; }

function loadState() {
  try {
    const data = JSON.parse(fs.readFileSync(statePath(), "utf8"));
    if (data && typeof data === "object" && data.owners) {
      if (!data.backup) data.backup = null;
      if (!data.owners) data.owners = {};
      return data;
    }
  } catch { /* 無檔 / 壞檔 → 空狀態 */ }
  return emptyState();
}

function saveState(state) {
  const p = statePath();
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(state, null, 2), "utf8");
}

function pruneOwners(state) {
  const now = Date.now() / 1000;
  const live = {};
  for (const [token, meta0] of Object.entries(state.owners || {})) {
    const meta = meta0 || {};
    const pid = parseInt(meta.pid || 0, 10);
    const ts = parseFloat(meta.ts || 0);
    if (now - ts > OWNER_TTL_SEC) continue;
    if (pid && !pidAlive(pid)) continue;
    live[token] = meta;
  }
  state.owners = live;
}

// ── Claude 套用 / 還原（只動 permissions）────────────────────────────────────
function applyClaude() {
  const p = claudeSettingsPath();
  if (!fs.existsSync(p)) {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, JSON.stringify({ permissions: CLAUDE_PERMISSIONS }, null, 2), "utf8");
    return { file_absent: true };
  }
  let data;
  try {
    data = JSON.parse(fs.readFileSync(p, "utf8"));
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("非物件");
  } catch (e) {
    console.warn("CodexAutoAI overlay: 讀取 Claude settings 失敗，略過套用：", e.message);
    return { skipped: true };
  }
  const backup = { file_absent: false, had_permissions: Object.prototype.hasOwnProperty.call(data, "permissions") };
  if (backup.had_permissions) backup.permissions = data.permissions;
  data.permissions = CLAUDE_PERMISSIONS;
  fs.writeFileSync(p, JSON.stringify(data, null, 2), "utf8");
  return backup;
}

function revertClaude(backup) {
  if (!backup || backup.skipped) return;
  const p = claudeSettingsPath();
  if (backup.file_absent) {
    let data = {};
    try { if (fs.existsSync(p)) data = JSON.parse(fs.readFileSync(p, "utf8")); } catch { return; }
    if (data && typeof data === "object") {
      delete data.permissions;
      if (Object.keys(data).length) fs.writeFileSync(p, JSON.stringify(data, null, 2), "utf8");
      else if (fs.existsSync(p)) fs.unlinkSync(p);
    }
    return;
  }
  if (!fs.existsSync(p)) return;
  let data;
  try { data = JSON.parse(fs.readFileSync(p, "utf8")); } catch { return; }
  if (!data || typeof data !== "object") return;
  if (backup.had_permissions) data.permissions = backup.permissions;
  else delete data.permissions;
  fs.writeFileSync(p, JSON.stringify(data, null, 2), "utf8");
}

// ── Codex 套用 / 還原（標記區塊，只動兩個頂層鍵）────────────────────────────
function codexBlock() {
  const body = Object.entries(CODEX_KEYS).map(([k, v]) => `${k} = "${v}"`);
  return [CODEX_BEGIN, ...body, CODEX_END].join("\n");
}

function stripCodexBlock(text) {
  const out = [];
  let skipping = false;
  for (const line of text.split("\n")) {
    if (line.trim() === CODEX_BEGIN) { skipping = true; continue; }
    if (skipping) { if (line.trim() === CODEX_END) skipping = false; continue; }
    out.push(line);
  }
  return out.join("\n");
}

function splitTopRegion(lines) {
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].replace(/^\s+/, "").startsWith("[")) return i;
  }
  return lines.length;
}

function applyCodex() {
  const p = codexConfigPath();
  const block = codexBlock();
  if (!fs.existsSync(p)) {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, block + "\n", "utf8");
    return { file_absent: true, removed_lines: [] };
  }
  let original;
  try { original = fs.readFileSync(p, "utf8"); }
  catch (e) { console.warn("CodexAutoAI overlay: 讀取 Codex config 失敗，略過套用：", e.message); return { skipped: true }; }
  const cleaned = stripCodexBlock(original);
  const lines = cleaned.split("\n");
  const cut = splitTopRegion(lines);
  const removed = lines.slice(0, cut).filter((ln) => CODEX_KEY_RE.test(ln));
  const keptTop = lines.slice(0, cut).filter((ln) => !CODEX_KEY_RE.test(ln));
  const rest = lines.slice(cut);
  const newText = [block, "", ...keptTop, ...rest].join("\n").replace(/\n+$/, "") + "\n";
  fs.writeFileSync(p, newText, "utf8");
  return { file_absent: false, removed_lines: removed };
}

function revertCodex(backup) {
  if (!backup || backup.skipped) return;
  const p = codexConfigPath();
  if (!fs.existsSync(p)) return;
  let text;
  try { text = fs.readFileSync(p, "utf8"); } catch { return; }
  const cleaned = stripCodexBlock(text);
  if (backup.file_absent) {
    if (cleaned.trim()) fs.writeFileSync(p, cleaned.replace(/\n+$/, "") + "\n", "utf8");
    else fs.unlinkSync(p);
    return;
  }
  const removed = backup.removed_lines || [];
  let out = cleaned;
  if (removed.length) {
    const lines = cleaned.split("\n");
    out = [...removed, ...lines].join("\n");
  }
  fs.writeFileSync(p, out.replace(/\n+$/, "") + "\n", "utf8");
}

// ── 公開 API ─────────────────────────────────────────────────────────────────
function disabled() { return !!(process.env.CODEXAUTOAI_NO_GLOBAL_OVERLAY || "").trim(); }

function acquire(token) {
  if (disabled()) return { ok: false, applied: false, reason: "disabled" };
  try {
    const state = loadState();
    pruneOwners(state);
    const first = Object.keys(state.owners).length === 0;
    if (first && !state.backup) {
      try { state.backup = { claude: applyClaude(), codex: applyCodex() }; }
      catch (e) { console.warn("CodexAutoAI overlay: 套用失敗：", e.message); state.backup = null; }
    }
    state.owners[token] = { pid: process.pid, ts: Date.now() / 1000 };
    saveState(state);
    return { ok: true, applied: first, owners: Object.keys(state.owners).length };
  } catch (e) {
    console.warn("CodexAutoAI overlay acquire 失敗：", e.message);
    return { ok: false, applied: false, reason: e.message };
  }
}

function release(token) {
  try {
    const state = loadState();
    delete state.owners[token];
    pruneOwners(state);
    let reverted = false;
    if (Object.keys(state.owners).length === 0) {
      const backup = state.backup || {};
      try { revertClaude(backup.claude); revertCodex(backup.codex); reverted = true; }
      catch (e) { console.warn("CodexAutoAI overlay: 還原失敗：", e.message); }
      try { if (fs.existsSync(statePath())) fs.unlinkSync(statePath()); }
      catch { state.backup = null; saveState(state); }
      return { ok: true, reverted, owners: 0 };
    }
    saveState(state);
    return { ok: true, reverted, owners: Object.keys(state.owners).length };
  } catch (e) {
    console.warn("CodexAutoAI overlay release 失敗：", e.message);
    return { ok: false, reverted: false, reason: e.message };
  }
}

function status() {
  const state = loadState();
  pruneOwners(state);
  return { active: Object.keys(state.owners).length > 0, owners: Object.keys(state.owners), applied: state.backup != null };
}

module.exports = {
  acquire, release, status,
  // 匯出內部函式供測試
  _internal: { applyClaude, revertClaude, applyCodex, revertCodex, stripCodexBlock, splitTopRegion, pidAlive, statePath, claudeSettingsPath, codexConfigPath },
};

// CLI（測試 / 手動）：node globalOverlay.js {acquire|release|status} [token]
if (require.main === module) {
  const [cmd, token] = process.argv.slice(2);
  if (cmd === "status") console.log(JSON.stringify(status()));
  else if (cmd === "acquire") console.log(JSON.stringify(acquire(token || `cli:${process.pid}`)));
  else if (cmd === "release") console.log(JSON.stringify(release(token || `cli:${process.pid}`)));
  else { console.log("usage: globalOverlay.js {acquire|release|status} [token]"); process.exit(2); }
}
