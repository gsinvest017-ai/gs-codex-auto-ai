// dashboard.js — CodexAutoAI 控制台（VS Code Webview 內嵌 GUI）。
// 給不想碰 CLI/TUI 的使用者：輸入需求 → 按鈕啟動（terminal 隱藏在背景跑）→
// 在面板上看七階段進度與「Claude 規劃 vs Codex 實作」分工證據（來源 log/events.jsonl，
// OBS-R2 事件：phase_start/phase_end/llm_call/tool_call）。純 Node + vanilla webview，無外部依賴。
const fs = require("fs");
const path = require("path");

const PHASES = ["初始化", "環境檢查", "需求分析", "架構設計", "審查", "並行開發", "測試", "交付"];

function phaseNum(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return v;
  const s = String(v).toLowerCase().replace(/^phase/, "").trim();
  return /^\d+$/.test(s) ? parseInt(s, 10) : null;
}

// 純函式：events.jsonl 各行 → 儀表板狀態（可直接單元測試）。
// 對齊 tools/progress.py 的 summarize，外加 Claude/Codex 分工統計。
function summarizeEvents(lines) {
  const s = {
    current: null, completed: [], iteration: 0, cost: 0, failed: false,
    claude: { calls: 0, inTok: 0, outTok: 0 },
    codex: { calls: 0 },
    codexInPhase5: 0,
    lastTs: null,
  };
  const completed = new Set();
  const failedPhases = new Set();   // -1 = 沒掛在任何 phase 底下的 run 層級錯誤
  let curPhase = null;
  for (const line of lines) {
    let ev;
    try { ev = JSON.parse(line); } catch { continue; } // 容忍半寫入行
    const t = ev.event_type;
    const p = phaseNum(ev.phase);
    if (t === "phase_start" && p !== null) { s.current = p; curPhase = p; }
    else if (t === "phase_end" && p !== null) {
      // 成功就把該 phase 之前的錯誤清掉——以前 s.failed 是全域 latch，
      // Phase 6 修復迴圈裡一次「下一輪就修好」的 escalation 會讓七階段跑完
      // 之後畫面還是紅的（跟 events_model.py 的 failed_phases 同語意）。
      if (ev.status === "success") { completed.add(p); failedPhases.delete(p); }
      else if (ev.status === "failure") failedPhases.add(p);
      curPhase = p;
    } else if (t === "llm_call") {
      s.claude.calls += 1;
      s.claude.inTok += ev["gen_ai.usage.input_tokens"] || 0;
      s.claude.outTok += ev["gen_ai.usage.output_tokens"] || 0;
    } else if (t === "tool_call" && /codex/i.test(String(ev.tool || ""))) {
      s.codex.calls += 1;
      if ((p !== null ? p : curPhase) === 5) s.codexInPhase5 += 1;
    } else if (t === "error") { failedPhases.add(p !== null ? p : -1); }
    if (ev.iteration !== undefined && ev.iteration !== null) s.iteration = ev.iteration;
    if (ev.cumulative_cost_usd !== undefined && ev.cumulative_cost_usd !== null) s.cost = ev.cumulative_cost_usd;
    if (ev.timestamp) s.lastTs = ev.timestamp;
  }
  s.completed = [...completed].sort((a, b) => a - b);
  s.failedPhases = [...failedPhases].sort((a, b) => a - b);
  s.failed = failedPhases.size > 0;
  // 分工警示：已進入/完成 phase5 但沒有任何 codex 呼叫 → 疑似「只燒 Claude、沒用 Codex 實作」
  const reached5 = (s.current !== null && s.current >= 5) || s.completed.includes(5);
  s.divisionWarning = reached5 && s.codexInPhase5 === 0;
  return s;
}

// 只保留 timestamp >= sinceMs 的 events.jsonl 行（濾掉舊 run 殘留）。sinceMs=0 時全保留
// （v2 orchestrator 的 events.jsonl 是當前 run 的、無 transcript 可界定時退回全用）。
function filterEventsSince(lines, sinceMs) {
  if (!sinceMs) return lines;
  return lines.filter((ln) => {
    try {
      const ts = JSON.parse(ln).timestamp;
      return !ts || Date.parse(ts) >= sinceMs;
    } catch { return false; }
  });
}

function readEventsFile(root) {
  const f = path.join(root, "log", "events.jsonl");
  if (!fs.existsSync(f)) return { exists: false, lines: [] };
  try {
    return { exists: true, lines: fs.readFileSync(f, "utf-8").split(/\r?\n/).filter(Boolean) };
  } catch { return { exists: true, lines: [] }; }
}

// ── Claude Code session transcript 資料源 ────────────────────────────────────
// events.jsonl 只有 v2 Python orchestrator 會寫；實際 pipeline 是 Claude Code 跑 skills，
// 真實證據在 ~/.claude/projects/<slug>/*.jsonl（ccusage 同源）：assistant 訊息帶
// usage tokens、tool_use 有 Skill(phaseN-…) 與 Bash(codex exec …)。

const os = require("os");

// Claude Code 專案目錄命名規則：路徑的 [:\/.] 全換成 '-'（含 . ——實測
// C:\Users\User\test-repo-0.9.8 → C--Users-User-test-repo-0-9-8；漏換 . 會找不到目錄）。
function projectSlug(root) {
  return String(root).replace(/[:\\/.]/g, "-");
}

// 解析 workspace 對應的 Claude 專案目錄：先試精準 slug，不中則對 projects/ 做
// case-insensitive 比對（VS Code fsPath 可能給小寫磁碟機代號 c:\ 而目錄是 C--…）。
function findProjectDir(root) {
  const base = path.join(os.homedir(), ".claude", "projects");
  const slug = projectSlug(root);
  const direct = path.join(base, slug);
  if (fs.existsSync(direct)) return direct;
  try {
    const want = slug.toLowerCase();
    for (const name of fs.readdirSync(base)) {
      if (name.toLowerCase() === want) return path.join(base, name);
    }
  } catch { /* projects 目錄不存在 */ }
  return null;
}

function findTranscript(root) {
  const dir = findProjectDir(root);
  if (!dir || !fs.existsSync(dir)) return null;
  let best = null;
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".jsonl")) continue;
    const f = path.join(dir, name);
    const mt = fs.statSync(f).mtimeMs;
    if (!best || mt > best.mtimeMs) best = { file: f, mtimeMs: mt };
  }
  return best ? best.file : null;
}

// skill 名稱 → 七階段編號（CLAUDE.md 的 phase 表）
const SKILL_PHASE = {
  "phase0-init": 0, "codex-env-check": 1, "phase2-requirements": 2,
  "phase3-architecture": 3, "phase4-review": 4, "phase5-build": 5,
  "phase6-test": 6, "phase7-delivery": 7,
};

// 純函式：transcript 各行 → 分工/進度統計（可單元測試）。
function summarizeTranscript(lines) {
  const s = {
    claude: { calls: 0, inTok: 0, outTok: 0, cacheTok: 0 },
    codex: { calls: 0 },
    codexInPhase5: 0,
    buildersInPhase5: 0, // phase5 內派遣的 Task/Agent 子代理數（builders 的 codex 在 subagents/）
    current: null,
    started: [],
    lastTs: null,
  };
  const started = new Set();
  for (const line of lines) {
    let d;
    try { d = JSON.parse(line); } catch { continue; }
    const m = d.message || {};
    const u = m.usage;
    if (u && (u.input_tokens || u.output_tokens)) {
      s.claude.calls += 1;
      s.claude.inTok += u.input_tokens || 0;
      s.claude.outTok += u.output_tokens || 0;
      s.claude.cacheTok += u.cache_read_input_tokens || 0;
    }
    const content = Array.isArray(m.content) ? m.content : [];
    for (const c of content) {
      if (!c) continue;
      // 與終端機同源的權威 phase 訊號：Claude 印在 assistant 文字的 [CodexAutoAI] Phase N/7
      // （CLAUDE.md「進度可見」規則）。新版 pipeline / Fable 不一定經 Skill tool，此為主源。
      if (c.type === "text" && typeof c.text === "string") {
        const pm = c.text.match(/Phase\s*(\d)\s*\/\s*7/);
        if (pm) { const p = +pm[1]; if (p >= 0 && p <= 7) { s.current = p; started.add(p); } }
        continue;
      }
      if (c.type !== "tool_use") continue;
      if (c.name === "Skill") {
        const skill = (c.input || {}).skill || "";
        const p = SKILL_PHASE[skill];
        if (p !== undefined) { s.current = p; started.add(p); }
        if (skill === "codex-run") {
          s.codex.calls += 1;
          if (s.current === 5) s.codexInPhase5 += 1;
        }
      } else if (c.name === "Task" || c.name === "Agent") {
        if (s.current === 5) s.buildersInPhase5 += 1;
      } else if (c.name === "Bash" || c.name === "PowerShell") {
        const cmd = String((c.input || {}).command || "");
        // codex exec（舊）與 codex_runner.py（0.9.5+ 防掛外殼）都算一次 Codex 呼叫。
        if (/codex\s+exec|codex_runner/i.test(cmd)) {
          s.codex.calls += 1;
          if (s.current === 5) s.codexInPhase5 += 1;
        }
      }
    }
    if (d.timestamp) { s.lastTs = d.timestamp; if (!s.firstTs) s.firstTs = d.timestamp; }
  }
  s.started = [...started].sort((a, b) => a - b);
  return s;
}

// 子代理 transcripts：phase5 builders 的 codex exec 不在主 transcript，而在
// <session>/subagents/agent-*.jsonl（主檔同名去掉 .jsonl 的目錄下）。每次 poll 全讀
// （單檔通常 <100KB），統計 codex 呼叫與 tokens。
function readSubagentStats(transcriptFile) {
  const out = { agents: 0, codexCalls: 0, inTok: 0, outTok: 0, cacheTok: 0 };
  if (!transcriptFile) return out;
  const dir = path.join(transcriptFile.replace(/\.jsonl$/i, ""), "subagents");
  if (!fs.existsSync(dir)) return out;
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".jsonl")) continue;
    out.agents += 1;
    let text;
    try { text = fs.readFileSync(path.join(dir, name), "utf-8"); } catch { continue; }
    for (const line of text.split(/\r?\n/)) {
      if (!line) continue;
      let d;
      try { d = JSON.parse(line); } catch { continue; }
      const m = d.message || {};
      const u = m.usage;
      if (u && (u.input_tokens || u.output_tokens)) {
        out.inTok += u.input_tokens || 0;
        out.outTok += u.output_tokens || 0;
        out.cacheTok += u.cache_read_input_tokens || 0;
      }
      for (const c of (Array.isArray(m.content) ? m.content : [])) {
        if (c && c.type === "tool_use" && (c.name === "Bash" || c.name === "PowerShell")
            && /codex\s+exec|codex_runner/i.test(String((c.input || {}).command || ""))) {
          out.codexCalls += 1;
        }
      }
    }
  }
  return out;
}

// 合併資料源（transcript 為主、events.jsonl 為輔、subagents 補 builders 的 codex、
// codexUsage 補 Codex 端真實 token 用量）。
function combineSummaries(ev, tr, sub, codexUsage) {
  sub = sub || { agents: 0, codexCalls: 0, inTok: 0, outTok: 0, cacheTok: 0 };
  codexUsage = codexUsage || { sessions: 0, inTok: 0, outTok: 0, cacheTok: 0 };
  const s = {
    current: tr && tr.current !== null ? tr.current : ev.current,
    completed: ev.completed,
    started: tr ? tr.started : [],
    iteration: ev.iteration,
    cost: ev.cost,
    failed: ev.failed,
    claude: {
      calls: ev.claude.calls + (tr ? tr.claude.calls : 0),
      inTok: ev.claude.inTok + (tr ? tr.claude.inTok : 0) + sub.inTok,
      outTok: ev.claude.outTok + (tr ? tr.claude.outTok : 0) + sub.outTok,
      cacheTok: (tr ? tr.claude.cacheTok : 0) + sub.cacheTok,
    },
    codex: {
      calls: ev.codex.calls + (tr ? tr.codex.calls : 0) + sub.codexCalls,
      sessions: codexUsage.sessions,
      inTok: codexUsage.inTok, outTok: codexUsage.outTok, cacheTok: codexUsage.cacheTok,
    },
    builders: (tr ? tr.buildersInPhase5 : 0) || sub.agents,
    lastTs: (tr && tr.lastTs) || ev.lastTs,
  };
  // 實作產出佔比（以 output tokens 比較——「誰在產內容」的公平單位，呼叫次數不可比）
  const codexOut = s.codex.outTok, claudeOut = s.claude.outTok;
  s.codexShare = (codexOut + claudeOut) > 0
    ? Math.round((codexOut / (codexOut + claudeOut)) * 100) : null;
  const marker = Math.max(
    s.current !== null && s.current !== undefined ? s.current : 0,
    s.completed.length ? Math.max(...s.completed) : 0,
    s.started.length ? Math.max(...s.started) : 0);
  // builders 是 phase5 派的子代理，其 codex 呼叫視為 phase5 的實作證據。
  s.codexInPhase5 = ev.codexInPhase5 + (tr ? tr.codexInPhase5 : 0)
    + (marker >= 5 ? sub.codexCalls : 0);
  // 「Codex 是否真的在做事」的權威訊號＝本次 run 的 codex sessions（readCodexUsage，
  // 讀 ~/.codex/sessions）。0.9.5 起 codex 走 codex_runner.py，主 transcript 的 Bash
  // 是 `python tools/codex_runner.py` 而非 `codex exec`，regex 抓不到 → 不能只靠它判警示。
  const codexActive = s.codexInPhase5 > 0 || s.codex.sessions > 0;
  // 分級：紅=進 phase5 後既沒派 builders、codex sessions 也是 0（真異常）；
  //       黃=已派 builders 但還沒看到任何 codex 紀錄（正常延遲，等待中）。
  const reached5 = marker >= 5;
  s.divisionWarning = reached5 && !codexActive && s.builders === 0;
  s.divisionWaiting = reached5 && !codexActive && s.builders > 0;
  s.marker = marker;
  return s;
}

// ── Codex token 用量（~/.codex/sessions rollout jsonl）──────────────────────
// Codex CLI 每次 exec 寫一個 rollout 檔：首行 session_meta（含 cwd）、event_msg 裡的
// token_count.info.total_token_usage 是「該 session 累計」——取最後一筆即總量。
// 121+ 檔每 2 秒重掃太重 → per-file cache（mtime+size 沒變就用上次解析結果）。
const _codexCache = new Map(); // file -> { mtimeMs, size, cwd, ts, usage }

function _parseRollout(file) {
  let cwd = null, usage = null;
  let text;
  try { text = fs.readFileSync(file, "utf-8"); } catch { return { cwd, usage }; }
  for (const line of text.split(/\r?\n/)) {
    if (!line) continue;
    let d;
    try { d = JSON.parse(line); } catch { continue; }
    const p = d.payload || {};
    if (d.type === "session_meta") cwd = p.cwd || null;
    else if (d.type === "event_msg" && p.type === "token_count") {
      const u = (p.info || {}).total_token_usage;
      if (u) usage = u; // 累計值，最後一筆為準
    }
  }
  return { cwd, usage };
}

// 彙總屬於此 workspace（cwd 相符）且 sinceMs 之後有活動的 codex sessions 用量。
function readCodexUsage(root, sinceMs) {
  const out = { sessions: 0, inTok: 0, outTok: 0, cacheTok: 0 };
  const base = path.join(os.homedir(), ".codex", "sessions");
  if (!fs.existsSync(base)) return out;
  const rootNorm = path.resolve(root).toLowerCase();
  const stack = [base];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { continue; }
    for (const ent of entries) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) { stack.push(full); continue; }
      if (!ent.name.endsWith(".jsonl")) continue;
      let st;
      try { st = fs.statSync(full); } catch { continue; }
      if (sinceMs && st.mtimeMs < sinceMs) continue; // 只算本次 run 期間的 session
      let rec = _codexCache.get(full);
      if (!rec || rec.mtimeMs !== st.mtimeMs || rec.size !== st.size) {
        const parsed = _parseRollout(full);
        rec = { mtimeMs: st.mtimeMs, size: st.size, cwd: parsed.cwd, usage: parsed.usage };
        _codexCache.set(full, rec);
      }
      if (!rec.cwd || path.resolve(rec.cwd).toLowerCase() !== rootNorm) continue;
      if (!rec.usage) continue;
      out.sessions += 1;
      out.inTok += rec.usage.input_tokens || 0;
      out.outTok += (rec.usage.output_tokens || 0) + (rec.usage.reasoning_output_tokens || 0);
      out.cacheTok += rec.usage.cached_input_tokens || 0;
    }
  }
  return out;
}

// 讀 transcript。刻意每次「整檔 UTF-8 讀取」而非 byte-offset 增量——
// 增量版曾在 CJK 密集的 transcript 上於 byte 邊界切斷多位元組字元、並讓面板卡在舊狀態
// （使用者實測 Phase 0/7 vs 終端 Phase 2 的元凶）。transcript ~1MB、每 2 秒全讀成本可忽略，
// 換取「面板永遠反映當前完整內容」的正確性。mtime 沒變就重用上次解析結果（省重複解析）。
let _trCache = { file: null, mtimeMs: 0, lines: [] };
function makeTranscriptReader() {
  return (root) => {
    const f = findTranscript(root);
    if (!f) { _trCache = { file: null, mtimeMs: 0, lines: [] }; return null; }
    let mt;
    try { mt = fs.statSync(f).mtimeMs; } catch { return null; }
    if (_trCache.file !== f || _trCache.mtimeMs !== mt) {
      let lines = [];
      try { lines = fs.readFileSync(f, "utf-8").split(/\r?\n/).filter(Boolean); } catch { /* 讀失敗保留舊 */ }
      _trCache = { file: f, mtimeMs: mt, lines };
    }
    return { lines: _trCache.lines, file: f };
  };
}

// GS gold 主題 webview（自足 inline，無外部資源）。
function html(defaultReq) {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  return `<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';">
<style>
  /* 全面採用 VS Code 主題變數（--vscode-*），面板自動跟 IDE 亮/暗主題一致；
     只保留一抹品牌金給標題與進度方塊。每個變數都帶 fallback 值（舊 VS Code / 非 webview 預覽用）。 */
  :root { --gold:#d4af37;
    --fg:var(--vscode-foreground,#e7ddc7); --muted:var(--vscode-descriptionForeground,#8b949e);
    --card:var(--vscode-editorWidget-background,#171a21); --line:var(--vscode-widget-border,#2a2f3a);
    --green:var(--vscode-charts-green,#3fb950); --red:var(--vscode-charts-red,#f85149); }
  body { background:var(--vscode-editor-background,#0f1115); color:var(--fg);
         font-family:var(--vscode-font-family,"Segoe UI",sans-serif); font-size:var(--vscode-font-size,13px); padding:16px 18px; }
  h1 { color:var(--gold); font-size:20px; margin:0 0 2px; }
  .sub { color:var(--muted); font-size:12px; margin-bottom:14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin-bottom:12px; }
  .card h2 { font-size:13px; color:var(--fg); margin:0 0 8px; }
  textarea { width:100%; box-sizing:border-box; background:var(--vscode-input-background,#0f1115);
             color:var(--vscode-input-foreground,#e7ddc7); border:1px solid var(--vscode-input-border,var(--line));
             border-radius:6px; padding:8px; font-size:13px; min-height:56px; resize:vertical; }
  .row { display:flex; gap:8px; margin-top:8px; flex-wrap:wrap; align-items:center; }
  button { border:0; border-radius:6px; padding:8px 14px; font-size:13px; cursor:pointer; }
  .primary { background:var(--vscode-button-background,var(--gold)); color:var(--vscode-button-foreground,#0f1115); font-weight:600; }
  .primary:hover { background:var(--vscode-button-hoverBackground,#c79a3e); }
  .ghost { background:var(--vscode-button-secondaryBackground,#21262d); color:var(--vscode-button-secondaryForeground,var(--fg)); }
  .ghost:hover { background:var(--vscode-button-secondaryHoverBackground,#2a3138); }
  .bar { font-family:var(--vscode-editor-font-family,Consolas,monospace); font-size:15px; letter-spacing:2px; color:var(--gold); }
  .muted { color:var(--muted); font-size:12px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .stat { background:var(--vscode-editor-background,#0f1115); border:1px solid var(--line); border-radius:6px; padding:8px 10px; }
  .stat b { color:var(--gold); font-size:16px; }
  .ok { color:var(--green); } .bad { color:var(--red); }
  .warn { background:var(--vscode-inputValidation-errorBackground,#2a1515); border:1px solid var(--vscode-inputValidation-errorBorder,var(--red));
          border-radius:6px; padding:8px 10px; color:var(--vscode-inputValidation-errorForeground,var(--red)); font-size:12px; margin-top:8px; display:none; }
  .wait { background:var(--vscode-inputValidation-warningBackground,#2a2410); border:1px solid var(--vscode-inputValidation-warningBorder,var(--gold));
          border-radius:6px; padding:8px 10px; color:var(--vscode-inputValidation-warningForeground,var(--gold)); font-size:12px; margin-top:8px; display:none; }
  label { font-size:12px; color:var(--muted); }
  #status { min-height:16px; }
</style></head><body>
<h1>CodexAutoAI 控制台</h1>
<div class="sub">全程免終端機：輸入需求 → 按啟動 → 在這裡看進度與分工。</div>

<div class="card"><h2>你想做什麼？</h2>
  <textarea id="req">${esc(defaultReq || "")}</textarea>
  <div class="row">
    <button class="primary" id="btnStart">🚀 啟動新任務</button>
    <button class="ghost" id="btnSeed">▶ 從 spec 開始</button>
    <label><input type="checkbox" id="autopilot" checked> 非停模式（全程不問，建議非開發者保持勾選）</label>
  </div>
  <div class="row muted" id="status"></div>
</div>

<div class="card"><h2>七階段進度</h2>
  <div class="bar" id="bar">░░░░░░░░</div>
  <div class="muted" id="phaseText">尚未開始——按上方「🚀 啟動新任務」。</div>
</div>

<div class="card"><h2>分工證據（確保 Claude 規劃、Codex 實作）</h2>
  <div class="grid">
    <div class="stat">Claude（規劃/調度）<br><b id="claudeCalls">0</b> 次呼叫<div class="muted" id="claudeTok">tokens —</div></div>
    <div class="stat">Codex（寫碼實作）<br><b id="codexCalls">0</b> 次呼叫<div class="muted" id="codexTok">tokens —</div><div class="muted" id="codexP5">phase5 內 0 次</div></div>
  </div>
  <div class="muted" id="share" style="margin-top:8px;"></div>
  <div class="wait" id="divWait">⏳ 已派遣 builder 子代理，等待第一筆 Codex 呼叫紀錄……（builders 的 codex exec 會稍晚出現在子代理 transcript）</div>
  <div class="warn" id="divWarn">⚠ 已進入並行開發（phase5）但既沒派遣 builder 也偵測不到任何 Codex 呼叫——疑似只在 Claude 上花 token、未走 Codex 實作，請檢查。</div>
  <div class="row muted"><span id="cost">累計成本 $0.0000</span><span id="iter"></span><span id="ts"></span></div>
</div>

<div class="row">
  <button class="ghost" id="btnPreview">🌐 即時預覽網頁 UI（內嵌）</button>
  <button class="ghost" id="btnTerm">🖥 顯示背景終端機（除錯用）</button>
</div>

<script>
  const vscode = acquireVsCodeApi();
  const $ = (id) => document.getElementById(id);
  $("btnStart").onclick = () => { vscode.postMessage({ type:"start", requirement:$("req").value, autopilot:$("autopilot").checked }); };
  $("btnSeed").onclick  = () => { vscode.postMessage({ type:"seed",  intent:$("req").value, autopilot:$("autopilot").checked }); };
  $("btnTerm").onclick  = () => { vscode.postMessage({ type:"showTerminal" }); };
  $("btnPreview").onclick = () => { vscode.postMessage({ type:"preview" }); };
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (m.type === "status") { $("status").textContent = m.text; return; }
    if (m.type !== "state") return;
    const s = m.summary;
    if (!m.exists) { $("phaseText").textContent = "尚未開始——按上方「🚀 啟動新任務」。"; return; }
    const marker = s.marker || 0;
    $("bar").textContent = Array.from({length:8}, (_,i) => i <= marker ? "▓" : "░").join("");
    const names = ${JSON.stringify(PHASES)};
    let state = s.failed ? "✗ 失敗/升級" : "● 進行中";
    if (marker === 7 && (s.completed.includes(7) || s.started.includes(7))) state = "✓ 交付階段";
    $("phaseText").innerHTML = "Phase " + marker + "/7 " + names[marker] + "　<span class='" + (s.failed ? "bad" : "ok") + "'>" + state + "</span>";
    $("claudeCalls").textContent = s.claude.calls;
    $("claudeTok").textContent = "tokens in " + s.claude.inTok + " / out " + s.claude.outTok
      + (s.claude.cacheTok ? "（cache " + s.claude.cacheTok + "）" : "");
    // 「次呼叫」以 codex sessions（~/.codex/sessions 的實際 codex 執行數）為權威——
    // 0.9.5 起走 codex_runner，transcript 的 codex exec 字樣抓不到、regex 計數會是 0。
    $("codexCalls").textContent = s.codex.sessions || s.codex.calls;
    $("codexTok").textContent = s.codex.sessions
      ? "tokens in " + s.codex.inTok + " / out " + s.codex.outTok
        + (s.codex.cacheTok ? "（cache " + s.codex.cacheTok + "）" : "") + "・" + s.codex.sessions + " sessions"
      : "tokens —（尚無本次 run 的 codex session）";
    $("codexP5").textContent = "phase5 內 " + s.codexInPhase5 + " 次"
      + (s.builders ? "・builders " + s.builders + " 個" : "");
    $("share").textContent = s.codexShare === null ? "" :
      "實作產出佔比（output tokens）：Codex " + s.codexShare + "% vs Claude " + (100 - s.codexShare) + "%"
      + (s.codexShare < 50 ? "　⚠ Codex 佔比偏低" : "　✓ 符合 Codex-first");
    $("divWarn").style.display = s.divisionWarning ? "block" : "none";
    $("divWait").style.display = s.divisionWaiting ? "block" : "none";
    $("cost").textContent = "累計成本 $" + (s.cost || 0).toFixed(4);
    $("iter").textContent = s.iteration ? "第 " + s.iteration + " 輪迭代" : "";
    $("ts").textContent = s.lastTs ? "最後事件 " + s.lastTs.replace("T", " ").slice(0, 19) : "";
  });
</script></body></html>`;
}

// 一次性計算某 workspace 的當前狀態（供狀態列 poller 用；面板 push() 有自己的增量版）。
// 純資料、不依賴 vscode——回傳 { exists, summary }（summary 同 combineSummaries）。
function computeState(root) {
  const { exists, lines } = readEventsFile(root);
  const f = findTranscript(root);
  let trSum = null, sub = null;
  if (f) {
    let trLines = [];
    try { trLines = fs.readFileSync(f, "utf-8").split(/\r?\n/).filter(Boolean); } catch { /* 讀失敗當空 */ }
    trSum = summarizeTranscript(trLines);
    sub = readSubagentStats(f);
  }
  const sinceMs = trSum && trSum.firstTs ? Date.parse(trSum.firstTs) - 60000 : 0;
  const summary = combineSummaries(
    summarizeEvents(filterEventsSince(lines, sinceMs)), trSum, sub, readCodexUsage(root, sinceMs));
  return { exists: exists || !!f, summary };
}

// 把一個 webview（面板或側欄 view 皆可）接上控制台：設 html、每 2s 推狀態、綁訊息。
// 回傳 dispose()（清 interval）。deps 由 extension.js 注入。
function wireDashboard(webview, deps) {
  const { root } = deps;
  webview.html = html(deps.defaultReq);
  const push = () => {
    const { exists, summary } = computeState(root);
    webview.postMessage({ type: "state", exists, summary });
  };
  const timer = setInterval(push, 2000);
  push();
  const sub = webview.onDidReceiveMessage((m) => {
    const reply = (text) => webview.postMessage({ type: "status", text });
    if (m.type === "start") deps.onStart(m.requirement, m.autopilot, reply);
    else if (m.type === "seed") deps.onSeed(m.intent, m.autopilot, reply);
    else if (m.type === "showTerminal") deps.onShowTerminal();
    else if (m.type === "preview" && deps.onPreview) deps.onPreview(reply);
  });
  return () => { clearInterval(timer); try { sub.dispose(); } catch { /* 已釋放 */ } };
}

// 開啟控制台「面板」（編輯器分頁）。deps 見 wireDashboard。
function openDashboard(deps) {
  const { vscode } = deps;
  const panel = vscode.window.createWebviewPanel(
    "codexautoaiDashboard", "CodexAutoAI 控制台",
    vscode.ViewColumn.One, { enableScripts: true, retainContextWhenHidden: true });
  const dispose = wireDashboard(panel.webview, deps);
  panel.onDidDispose(dispose);
  return panel;
}

// 側欄「view」的 WebviewViewProvider（常駐活動列）。makeDeps() 於 view 解析時取當前 root + callbacks。
function makeDashboardViewProvider(makeDeps) {
  return {
    resolveWebviewView(view) {
      view.webview.options = { enableScripts: true };
      const dispose = wireDashboard(view.webview, makeDeps());
      view.onDidDispose(dispose);
    },
  };
}

module.exports = {
  openDashboard, makeDashboardViewProvider, computeState, PHASES, summarizeEvents, readEventsFile, filterEventsSince,
  summarizeTranscript, combineSummaries, projectSlug, findTranscript, findProjectDir,
  makeTranscriptReader, readSubagentStats, readCodexUsage,
};
