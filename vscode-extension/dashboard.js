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
  let curPhase = null;
  for (const line of lines) {
    let ev;
    try { ev = JSON.parse(line); } catch { continue; } // 容忍半寫入行
    const t = ev.event_type;
    const p = phaseNum(ev.phase);
    if (t === "phase_start" && p !== null) { s.current = p; curPhase = p; }
    else if (t === "phase_end" && p !== null) {
      if (ev.status === "success") completed.add(p);
      else if (ev.status === "failure") s.failed = true;
      curPhase = p;
    } else if (t === "llm_call") {
      s.claude.calls += 1;
      s.claude.inTok += ev["gen_ai.usage.input_tokens"] || 0;
      s.claude.outTok += ev["gen_ai.usage.output_tokens"] || 0;
    } else if (t === "tool_call" && /codex/i.test(String(ev.tool || ""))) {
      s.codex.calls += 1;
      if ((p !== null ? p : curPhase) === 5) s.codexInPhase5 += 1;
    } else if (t === "error") { s.failed = true; }
    if (ev.iteration !== undefined && ev.iteration !== null) s.iteration = ev.iteration;
    if (ev.cumulative_cost_usd !== undefined && ev.cumulative_cost_usd !== null) s.cost = ev.cumulative_cost_usd;
    if (ev.timestamp) s.lastTs = ev.timestamp;
  }
  s.completed = [...completed].sort((a, b) => a - b);
  // 分工警示：已進入/完成 phase5 但沒有任何 codex 呼叫 → 疑似「只燒 Claude、沒用 Codex 實作」
  const reached5 = (s.current !== null && s.current >= 5) || s.completed.includes(5);
  s.divisionWarning = reached5 && s.codexInPhase5 === 0;
  return s;
}

function readEventsFile(root) {
  const f = path.join(root, "log", "events.jsonl");
  if (!fs.existsSync(f)) return { exists: false, lines: [] };
  try {
    return { exists: true, lines: fs.readFileSync(f, "utf-8").split(/\r?\n/).filter(Boolean) };
  } catch { return { exists: true, lines: [] }; }
}

// GS gold 主題 webview（自足 inline，無外部資源）。
function html(defaultReq) {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  return `<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';">
<style>
  :root { --bg:#0f1115; --card:#171a21; --gold:#d4af37; --champ:#e7ddc7; --muted:#8b949e; --green:#3fb950; --red:#f85149; }
  body { background:var(--bg); color:var(--champ); font-family:"Segoe UI",sans-serif; padding:16px 18px; }
  h1 { color:var(--gold); font-size:20px; margin:0 0 2px; }
  .sub { color:var(--muted); font-size:12px; margin-bottom:14px; }
  .card { background:var(--card); border-radius:8px; padding:12px 14px; margin-bottom:12px; }
  .card h2 { font-size:13px; color:var(--champ); margin:0 0 8px; }
  textarea { width:100%; box-sizing:border-box; background:#0f1115; color:var(--champ); border:1px solid #2a2f3a;
             border-radius:6px; padding:8px; font-size:13px; min-height:56px; resize:vertical; }
  .row { display:flex; gap:8px; margin-top:8px; flex-wrap:wrap; align-items:center; }
  button { border:0; border-radius:6px; padding:8px 14px; font-size:13px; cursor:pointer; }
  .primary { background:var(--gold); color:#0f1115; font-weight:600; }
  .ghost { background:#21262d; color:var(--champ); }
  .bar { font-family:Consolas,monospace; font-size:15px; letter-spacing:2px; }
  .muted { color:var(--muted); font-size:12px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .stat { background:#0f1115; border-radius:6px; padding:8px 10px; }
  .stat b { color:var(--gold); font-size:16px; }
  .ok { color:var(--green); } .bad { color:var(--red); }
  .warn { background:#2a1515; border:1px solid var(--red); border-radius:6px; padding:8px 10px;
          color:var(--red); font-size:12px; margin-top:8px; display:none; }
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
    <div class="stat">Codex（寫碼實作）<br><b id="codexCalls">0</b> 次呼叫<div class="muted" id="codexP5">phase5 內 0 次</div></div>
  </div>
  <div class="warn" id="divWarn">⚠ 已進入並行開發（phase5）但偵測不到任何 Codex 呼叫——疑似只在 Claude 上花 token、未走 Codex 實作，請檢查。</div>
  <div class="row muted"><span id="cost">累計成本 $0.0000</span><span id="iter"></span><span id="ts"></span></div>
</div>

<div class="row">
  <button class="ghost" id="btnTerm">🖥 顯示背景終端機（除錯用）</button>
</div>

<script>
  const vscode = acquireVsCodeApi();
  const $ = (id) => document.getElementById(id);
  $("btnStart").onclick = () => { vscode.postMessage({ type:"start", requirement:$("req").value, autopilot:$("autopilot").checked }); };
  $("btnSeed").onclick  = () => { vscode.postMessage({ type:"seed",  intent:$("req").value, autopilot:$("autopilot").checked }); };
  $("btnTerm").onclick  = () => { vscode.postMessage({ type:"showTerminal" }); };
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (m.type === "status") { $("status").textContent = m.text; return; }
    if (m.type !== "state") return;
    const s = m.summary;
    if (!m.exists) { $("phaseText").textContent = "尚未開始——按上方「🚀 啟動新任務」。"; return; }
    const marker = Math.max(s.current ?? 0, s.completed.length ? Math.max(...s.completed) : 0);
    $("bar").textContent = Array.from({length:8}, (_,i) => i <= marker ? "▓" : "░").join("");
    const names = ${JSON.stringify(PHASES)};
    let state = s.failed ? "✗ 失敗/升級" : "● 進行中";
    if (marker === 7 && s.completed.includes(7)) state = "✓ 完成";
    $("phaseText").innerHTML = "Phase " + marker + "/7 " + names[marker] + "　<span class='" + (s.failed ? "bad" : "ok") + "'>" + state + "</span>";
    $("claudeCalls").textContent = s.claude.calls;
    $("claudeTok").textContent = "tokens in " + s.claude.inTok + " / out " + s.claude.outTok;
    $("codexCalls").textContent = s.codex.calls;
    $("codexP5").textContent = "phase5 內 " + s.codexInPhase5 + " 次";
    $("divWarn").style.display = s.divisionWarning ? "block" : "none";
    $("cost").textContent = "累計成本 $" + (s.cost || 0).toFixed(4);
    $("iter").textContent = s.iteration ? "第 " + s.iteration + " 輪迭代" : "";
    $("ts").textContent = s.lastTs ? "最後事件 " + s.lastTs.replace("T", " ").slice(0, 19) : "";
  });
</script></body></html>`;
}

// 開啟控制台面板。deps 由 extension.js 注入（避免此檔依賴 vscode 以外的東西）：
//   { vscode, root, defaultReq, onStart(requirement, autopilot, reply), onSeed(intent, autopilot, reply), onShowTerminal() }
function openDashboard(deps) {
  const { vscode, root } = deps;
  const panel = vscode.window.createWebviewPanel(
    "codexautoaiDashboard", "CodexAutoAI 控制台",
    vscode.ViewColumn.One, { enableScripts: true, retainContextWhenHidden: true });
  panel.webview.html = html(deps.defaultReq);

  const push = () => {
    const { exists, lines } = readEventsFile(root);
    panel.webview.postMessage({ type: "state", exists, summary: summarizeEvents(lines) });
  };
  const timer = setInterval(push, 2000);
  push();

  panel.webview.onDidReceiveMessage((m) => {
    const reply = (text) => panel.webview.postMessage({ type: "status", text });
    if (m.type === "start") deps.onStart(m.requirement, m.autopilot, reply);
    else if (m.type === "seed") deps.onSeed(m.intent, m.autopilot, reply);
    else if (m.type === "showTerminal") deps.onShowTerminal();
  });
  panel.onDidDispose(() => clearInterval(timer));
  return panel;
}

module.exports = { openDashboard, summarizeEvents, readEventsFile };
