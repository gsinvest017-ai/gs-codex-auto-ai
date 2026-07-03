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

// 偵測 server 型專案的啟動入口（依序偏好；純函式可測）。回傳 { cmd, label } 或 null。
function detectLauncher(root) {
  const has = (f) => fs.existsSync(path.join(root, f));
  if (process.platform === "win32" && has("run.ps1")) {
    return { cmd: `powershell -NoProfile -ExecutionPolicy Bypass -File .\\run.ps1`, label: "run.ps1" };
  }
  if (process.platform !== "win32" && has("run.sh")) {
    return { cmd: "bash ./run.sh", label: "run.sh" };
  }
  if (has("package.json")) {
    try {
      const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf-8"));
      const scripts = pkg.scripts || {};
      if (scripts.dev) return { cmd: "npm run dev", label: "npm run dev" };
      if (scripts.start) return { cmd: "npm start", label: "npm start" };
    } catch { /* 壞 package.json 略過 */ }
  }
  if (has("manage.py")) return { cmd: "python manage.py runserver", label: "manage.py runserver" };
  if (has("app.py")) return { cmd: "python app.py", label: "python app.py" };
  if (has("main.py")) return { cmd: "python main.py", label: "python main.py" };
  return null;
}

function isPortOpen(port) {
  return new Promise((resolve) => {
    const sock = net.connect({ port, host: "127.0.0.1" }, () => { sock.destroy(); resolve(true); });
    sock.on("error", () => resolve(false));
    sock.setTimeout(300, () => { sock.destroy(); resolve(false); });
  });
}

async function waitForPort(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await isPortOpen(port)) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
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
  // 沒有靜態頁：server 型專案。① server 已在跑（port 開著）→ 直接內嵌預覽；
  // ② 有啟動入口（run.ps1 / npm dev / app.py…）→ 背景一鍵啟動、等 port 開再預覽；
  // ③ 都不行 → 問 URL（預設帶偵測值）。
  const url = guessServerUrl(root);
  const port = parseInt(url.split(":").pop(), 10);
  if (await isPortOpen(port)) {
    await vscodeApi.simpleBrowser(url);
    return { mode: "urlLive", detail: url };
  }
  const launcher = detectLauncher(root);
  if (launcher && vscodeApi.runServer) {
    vscodeApi.runServer(launcher.cmd, launcher.label);
    if (await waitForPort(port, 45000)) {
      await vscodeApi.simpleBrowser(url);
      return { mode: "serverStarted", detail: `${launcher.label} → ${url}` };
    }
    // 起了但偵測埠一直沒開（埠猜錯/啟動慢）→ 落到問 URL
  }
  const asked = await vscodeApi.askUrl(url);
  if (!asked) return { mode: "cancelled", detail: "" };
  await vscodeApi.simpleBrowser(asked);
  return { mode: "url", detail: asked };
}

module.exports = {
  findWebRoots, guessServerUrl, getFreePort, ensureStaticServer, killAllServers,
  openPreview, detectLauncher, isPortOpen, waitForPort,
};
