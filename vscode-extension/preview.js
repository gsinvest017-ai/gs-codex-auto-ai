// preview.js — 前端網頁 UI 的內嵌即時預覽（純 Node 可單測，vscode API 由 extension.js 注入）。
//
// 三層降級（survey 結論，全部在 VS Code 內嵌、非開發者零操作）：
//   1. MS Live Preview（ms-vscode.live-server）有裝 → `livePreview.start.preview.atFile`
//      （內嵌瀏覽器 + hot reload，最佳體驗）
//   2. 未裝 → 自起 `python -m http.server`（Python 是 CodexAutoAI 既有前置）+
//      VS Code 內建 Simple Browser（`simpleBrowser.show`，零安裝）
//   3. 找不到靜態頁（如 Flask/FastAPI 這類 server 專案）→ 輸入/偵測 URL 走 Simple Browser
const fs = require("fs");
const net = require("net");
const path = require("path");
const { spawn } = require("child_process");

// 常見前端產出位置（依序偏好）：pipeline 產的專案多在 src/ 或根目錄
const WEB_DIRS = ["", "web", "public", "frontend", "dist", "build", "docs", "src", "static"];

// 找 workspace 下的網頁進入點（index.html 優先，找不到才收根目錄第一個 *.html）。
// 回傳相對路徑清單（依 WEB_DIRS 偏好排序）。
function findWebRoots(root) {
  const hits = [];
  for (const d of WEB_DIRS) {
    const f = path.join(root, d, "index.html");
    if (fs.existsSync(f)) hits.push(path.relative(root, f));
  }
  if (!hits.length) {
    try {
      for (const name of fs.readdirSync(root)) {
        if (name.toLowerCase().endsWith(".html")) { hits.push(name); break; }
      }
    } catch { /* 忽略讀取失敗 */ }
  }
  return hits;
}

// 偵測常見 server 專案的預設 URL（給第 3 層當提示，不自動啟動 server——那是 pipeline 的事）。
function guessServerUrl(root) {
  const reads = (f) => { try { return fs.readFileSync(path.join(root, f), "utf-8"); } catch { return ""; } };
  const texts = reads("run.ps1") + reads("run.sh") + reads("README.md") + reads("app.py") + reads("main.py");
  // 兩種常見寫法：URL 相鄰（localhost:8080）與 Flask/uvicorn 風格（port=8123 / --port 8123）
  const m = texts.match(/(?:https?:\/\/)?(?:localhost|127\.0\.0\.1):(\d{4,5})/)
    || texts.match(/port[\s=:]+["']?(\d{4,5})/i);
  return m ? `http://127.0.0.1:${m[1]}` : "http://127.0.0.1:8000";
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

// 靜態 server 生命週期：同一 serveDir 重用；extension deactivate 時 killAll。
const _servers = new Map(); // serveDir -> { port, proc }

async function ensureStaticServer(serveDir) {
  const existing = _servers.get(serveDir);
  if (existing && existing.proc.exitCode === null) return existing.port;
  const port = await getFreePort();
  const proc = spawn("python", ["-m", "http.server", String(port), "--bind", "127.0.0.1"],
    { cwd: serveDir, stdio: "ignore", windowsHide: true });
  _servers.set(serveDir, { port, proc });
  // 等 server 可連（最多 ~3 秒）
  for (let i = 0; i < 15; i++) {
    const ok = await new Promise((resolve) => {
      const sock = net.connect({ port, host: "127.0.0.1" }, () => { sock.destroy(); resolve(true); });
      sock.on("error", () => resolve(false));
      sock.setTimeout(200, () => { sock.destroy(); resolve(false); });
    });
    if (ok) return port;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("static server 啟動逾時（python -m http.server）");
}

function killAllServers() {
  for (const { proc } of _servers.values()) {
    try { proc.kill(); } catch { /* 已結束 */ }
  }
  _servers.clear();
}

// 主流程：回傳 { mode, detail } 給呼叫端顯示。vscodeApi 需提供
// { hasLivePreview(), livePreviewAtFile(absPath), simpleBrowser(url), askUrl(defaultUrl) }。
async function openPreview(root, vscodeApi, { pickIndex = 0, urlOverride = null } = {}) {
  if (urlOverride) {
    await vscodeApi.simpleBrowser(urlOverride);
    return { mode: "url", detail: urlOverride };
  }
  const hits = findWebRoots(root);
  if (hits.length) {
    const rel = hits[Math.min(pickIndex, hits.length - 1)];
    const abs = path.join(root, rel);
    if (vscodeApi.hasLivePreview()) {
      await vscodeApi.livePreviewAtFile(abs);
      return { mode: "livePreview", detail: rel };
    }
    const port = await ensureStaticServer(path.dirname(abs));
    const url = `http://127.0.0.1:${port}/${path.basename(abs)}`;
    await vscodeApi.simpleBrowser(url);
    return { mode: "staticServer", detail: url };
  }
  // 沒有靜態頁：server 型專案 → 問 URL（預設帶偵測值）
  const url = await vscodeApi.askUrl(guessServerUrl(root));
  if (!url) return { mode: "cancelled", detail: "" };
  await vscodeApi.simpleBrowser(url);
  return { mode: "url", detail: url };
}

module.exports = { findWebRoots, guessServerUrl, getFreePort, ensureStaticServer, killAllServers, openPreview };
