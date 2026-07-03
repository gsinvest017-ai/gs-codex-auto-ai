// CodexAutoAI VS Code extension — 啟動器（自帶框架快照）。
// 面板四個指令：安裝設定（初始化＋登入修復合一）、啟動新任務（輸入需求跑 claude）、
// 啟動新任務：從 spec 開始（gs-spec-forge 產 spec 再跑 pipeline）、檢查更新。
// codexautoai.init 仍註冊供舊 keybinding/腳本呼叫，但已從命令面板移除（setup 涵蓋其功能）。
// 純 vscode API + Node 內建模組（fs / path / https / child_process），無第三方依賴。
const vscode = require("vscode");
const fs = require("fs");
const os = require("os");
const path = require("path");
const https = require("https");
const { execFile, exec } = require("child_process");
const globalOverlay = require("./globalOverlay"); // 啟動套用 / 關閉還原全域 Claude/Codex 設定
const specforge = require("./specforge"); // spec-forge 候選解析 + 逐一嘗試（含內建快照 fallback）
const dashboard = require("./dashboard"); // 控制台（webview 內嵌 GUI，非開發者免 CLI）

// 最近一次背景啟動的 terminal（控制台「顯示終端機」逃生口用）。
let lastTerminal = null;

// 在 root 開 terminal 跑 claude；hidden=true 時不搶焦點（控制台走這條，非開發者不用看 CLI）。
function runClaudeInTerminal(root, inner, { hidden = false } = {}) {
  const t = vscode.window.createTerminal({ name: "CodexAutoAI", cwd: root, hideFromUser: !!hidden });
  lastTerminal = t;
  if (!hidden) t.show();
  if (process.platform === "win32") { t.sendText(`Set-Location -LiteralPath "${root}"`); }
  t.sendText(inner);
  return t;
}

// 需求 → claude 啟動指令（與 start 命令同語意；autopilot=true 走非停模式）。
function buildInner(requirement, autopilot) {
  const safe = (requirement || "").replace(/"/g, "'").trim();
  if (autopilot) return `claude "/autopilot on ${safe}"`;
  return safe ? `claude "${safe}"` : "claude";
}

// 產 spec 再啟動（start 與控制台共用）。回傳 Promise<{ok, specPath?, error?}>。
function seedThenBuildInner(root, intent, cfg) {
  const cands = specforge.candidates(cfg.get("specForgeCmd", "spec-forge"),
    __dirname);
  const env = Object.assign({}, process.env, { SPEC_VAULT: path.join(root, "vault") });
  const safeIntent = (intent || "").replace(/"/g, "'").trim();
  return specforge.trySeed(cands, safeIntent,
    { cwd: root, env, timeout: 60000, windowsHide: true }, exec);
}

// 本 extension host 持有的 overlay owner token（deactivate 時用同一個 token release）。
let overlayToken = null;

// extension 以 .vsix 發佈（非 Marketplace），更新來源為 GitHub Release（tag 前綴 ext-v）。
// 指向 PUBLIC 發行鏡像 repo（原始碼私有）：公開 repo 讀 releases 免 token，任何使用者都能檢查更新。
const REPO = process.env.CODEXAUTOAI_UPDATE_REPO || "gsinvest017-ai/gs-codex-auto-ai-releases";
const TAG_PREFIX = "ext-v";
const CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000; // 每天最多自動查一次

function workspaceRoot() {
  const f = vscode.workspace.workspaceFolders;
  return f && f.length ? f[0].uri.fsPath : null;
}

// spec-forge 解析與執行已抽到 specforge.js（候選鏈：明確設定 > venv > PATH > 內建快照），
// 前面候選失敗會自動落到下一個——venv 斷根（Python 升級）、沒裝 gs-spec-forge、沒 gh、
// 沒 private repo 權限的使用者都能靠內建快照開箱即用（唯一前置：Python）。

function hasFramework(root) {
  return fs.existsSync(path.join(root, "CLAUDE.md")) &&
         fs.existsSync(path.join(root, ".claude"));
}

// 把 extension 自帶的 framework/ 快照複製進 workspace。
// 預設「已存在不覆蓋」（保護使用者既有檔）；opts.force 列出的頂層項目一律以 bundled 版覆蓋。
// 「設定/修復」用 force 帶 launcher（setup.ps1/cmd/sh），確保舊專案的過期/壞掉 launcher 會被修到最新。
function copyFramework(extPath, root, opts = {}) {
  const force = new Set(opts.force || []);
  const src = path.join(extPath, "framework");
  if (!fs.existsSync(src)) {
    vscode.window.showErrorMessage("CodexAutoAI: 找不到內建框架快照，請用 build-vsix 重新打包。");
    return false;
  }
  for (const entry of fs.readdirSync(src)) {
    const s = path.join(src, entry), d = path.join(root, entry);
    if (fs.existsSync(d) && !force.has(entry)) continue; // 不覆蓋使用者既有檔（force 清單除外）
    fs.cpSync(s, d, { recursive: true, force: true });
  }
  return true;
}

function termInRoot(root, name) {
  return vscode.window.createTerminal({ name, cwd: root });
}

// ── 環境 pre-check（已安裝+登入就跳過「設定/修復」，不開終端機）──────────────
// claude / codex 在 Windows 是 .cmd 蓋子，需要 shell；用 exec(字串) 避開 execFile 對 .cmd
// 的 FileNotFoundError，也避開 shell:true + args 陣列的 DEP0190 警告。回傳 Promise<bool>。
function runOk(cmd, timeout = 8000) {
  return new Promise((resolve) => {
    exec(cmd, { timeout, windowsHide: true }, (err) => resolve(!err));
  });
}

// 各回傳 { ok, name, msg }；ok=true 代表「已安裝且已登入」，毋須設定。
async function checkClaude() {
  if (!(await runOk("claude --version"))) return { ok: false, name: "Claude Code", msg: "未安裝" };
  // 登入判斷與桌面 App 一致：~/.claude/.credentials.json 是否存在。
  const cred = path.join(os.homedir(), ".claude", ".credentials.json");
  return fs.existsSync(cred)
    ? { ok: true, name: "Claude Code" }
    : { ok: false, name: "Claude Code", msg: "未登入" };
}

async function checkCodex() {
  if (!(await runOk("codex --version"))) return { ok: false, name: "Codex", msg: "未安裝" };
  return (await runOk("codex login status"))
    ? { ok: true, name: "Codex" }
    : { ok: false, name: "Codex", msg: "未登入" };
}

async function checkGh() {
  if (!(await runOk("gh --version"))) return { ok: false, name: "GitHub CLI", msg: "未安裝" };
  return (await runOk("gh auth status"))
    ? { ok: true, name: "GitHub CLI" }
    : { ok: false, name: "GitHub CLI", msg: "未登入" };
}

// ── 版本檢查（借鏡 autogo updater：private repo 需 token、永不 throw）─────────────
function parseVer(s) {
  const m = /(\d+)\.(\d+)\.(\d+)/.exec(s || "");
  return m ? [+m[1], +m[2], +m[3]] : null;
}

function isNewer(latest, current) {
  const a = parseVer(latest), b = parseVer(current);
  if (!a || !b) return false;
  for (let i = 0; i < 3; i++) { if (a[i] !== b[i]) return a[i] > b[i]; }
  return false;
}

// 找得到就用的 gh 可執行檔清單：PATH 上的 gh(.exe) + 常見絕對安裝路徑。
// 修「終端機打 gh 可以，但 GUI 開的 VS Code extension host PATH 抓不到 gh」的情況。
function ghCandidates() {
  const list = [process.platform === "win32" ? "gh.exe" : "gh"];
  if (process.platform === "win32") {
    const home = os.homedir();
    list.push(
      "C:\\Program Files\\GitHub CLI\\gh.exe",
      "C:\\Program Files (x86)\\GitHub CLI\\gh.exe",
      path.join(home, "scoop", "shims", "gh.exe"),
      path.join(home, "AppData", "Local", "Microsoft", "WinGet", "Links", "gh.exe"),
      path.join(home, ".local", "bin", "gh.exe"),
    );
  } else {
    list.push("/usr/bin/gh", "/usr/local/bin/gh", "/opt/homebrew/bin/gh",
      path.join(os.homedir(), ".local", "bin", "gh"));
  }
  return list;
}

// 依序試 gh 候選路徑跑 `gh auth token`，第一個成功的回傳 token。全失敗回 null。
function ghTokenFromCli(cands, i = 0) {
  if (i >= cands.length) return Promise.resolve(null);
  return new Promise((resolve) => {
    // execFile 免 shell（gh 是真 exe，非 .cmd 蓋子），也避開 shell:true + args 的 DEP0190 警告。
    execFile(cands[i], ["auth", "token"], { timeout: 6000 }, (err, stdout) => {
      const tok = err ? null : (stdout || "").trim() || null;
      resolve(tok);
    });
  }).then((tok) => tok || ghTokenFromCli(cands, i + 1));
}

// token 解析：環境變數 → gh CLI（多路徑）。回傳 Promise<string|null>。
// 注意：更新來源已改為 PUBLIC 鏡像 repo，token 只用來提高 API rate limit，**非必需**。
function ghToken() {
  for (const k of ["CODEXAUTOAI_GH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"]) {
    const v = (process.env[k] || "").trim();
    if (v) return Promise.resolve(v);
  }
  return ghTokenFromCli(ghCandidates());
}

// GET https://api.github.com<path>，帶 auth。回傳 Promise<parsed|null>，永不 reject。
function apiGet(apiPath, token) {
  return new Promise((resolve) => {
    const headers = {
      "Accept": "application/vnd.github+json",
      "User-Agent": "codexautoai-vsix-updater",
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const req = https.request(
      { host: "api.github.com", path: apiPath, method: "GET", headers, timeout: 8000 },
      (res) => {
        let buf = "";
        res.on("data", (c) => (buf += c));
        res.on("end", () => {
          try { resolve(res.statusCode < 300 ? JSON.parse(buf) : null); }
          catch { resolve(null); }
        });
      }
    );
    req.on("error", () => resolve(null));
    req.on("timeout", () => { req.destroy(); resolve(null); });
    req.end();
  });
}

// 從 raw.githubusercontent 抓 latest.json（靜態檔，**不吃 api.github.com 的 60/hr 匿名限額**）。
// 對「沒裝 gh / 公司 NAT 多人共用對外 IP」的使用者更可靠——那些情境走 API 常被 403 rate-limit。
// 回傳 Promise<parsed|null>，永不 reject。
function fetchManifest() {
  return new Promise((resolve) => {
    const req = https.request(
      { host: "raw.githubusercontent.com", path: `/${REPO}/main/latest.json`,
        method: "GET", timeout: 8000,
        headers: { "User-Agent": "codexautoai-vsix-updater", "Accept": "application/json" } },
      (res) => {
        let buf = "";
        res.on("data", (c) => (buf += c));
        res.on("end", () => { try { resolve(res.statusCode < 300 ? JSON.parse(buf) : null); } catch { resolve(null); } });
      }
    );
    req.on("error", () => resolve(null));
    req.on("timeout", () => { req.destroy(); resolve(null); });
    req.end();
  });
}

// 取最新的 extension release（tag 以 ext-v 開頭），與桌面 app-v* 區隔。
async function latestExtRelease(token) {
  const list = await apiGet(`/repos/${REPO}/releases?per_page=30`, token);
  if (!Array.isArray(list)) return null;
  let best = null, bestVer = null;
  for (const rel of list) {
    if (rel.draft) continue;
    const tag = rel.tag_name || "";
    if (!tag.startsWith(TAG_PREFIX)) continue;
    const ver = parseVer(tag);
    if (!ver) continue;
    if (!bestVer || isNewer(tag, `${bestVer[0]}.${bestVer[1]}.${bestVer[2]}`)) {
      best = rel; bestVer = ver;
    }
  }
  return best;
}

// 主流程：查到新版跳通知；manual=true 時連「已最新 / 查不到」也回報。
async function checkForUpdate(context, { manual = false } = {}) {
  const current = (context.extension && context.extension.packageJSON &&
                   context.extension.packageJSON.version) || "0.0.0";

  if (!manual) {
    const cfg = vscode.workspace.getConfiguration("codexautoai");
    if (cfg.get("checkForUpdates", true) === false) return;
    const last = context.globalState.get("lastUpdateCheck", 0);
    if (Date.now() - last < CHECK_INTERVAL_MS) return;
    context.globalState.update("lastUpdateCheck", Date.now());
  }

  // 先試 raw 靜態 manifest（免 API 限額，最可靠）；抓不到再退回 releases API。
  let latest = null, dlUrl = null, htmlUrl = null;
  const manifest = await fetchManifest();
  if (manifest && manifest.ext && manifest.ext.version) {
    latest = String(manifest.ext.version);
    dlUrl = manifest.ext.vsix || null;
    htmlUrl = `https://github.com/${REPO}/releases/tag/${manifest.ext.tag || (TAG_PREFIX + latest)}`;
  } else {
    const token = await ghToken(); // 公開鏡像免 token；有 token 只是提高 API rate limit
    const rel = await latestExtRelease(token);
    if (!rel) {
      if (manual) {
        vscode.window.showWarningMessage(
          `CodexAutoAI: 讀不到更新資訊（網路 / GitHub 連線問題，或 API 流量限制）。更新來源：${REPO}。稍後再試。`);
      }
      return;
    }
    const tag = rel.tag_name || "";
    latest = tag.startsWith(TAG_PREFIX) ? tag.slice(TAG_PREFIX.length) : tag.replace(/^v/, "");
    const vsix = (rel.assets || []).find((a) => /\.vsix$/i.test(a.name || ""));
    dlUrl = (vsix && vsix.browser_download_url) || rel.html_url;
    htmlUrl = rel.html_url;
  }

  if (!isNewer(latest, current)) {
    if (manual) vscode.window.showInformationMessage(`CodexAutoAI: 已是最新版 v${current}。`);
    return;
  }

  if (!dlUrl) dlUrl = htmlUrl;
  const picks = ["下載 .vsix", "查看 Release", "不再提醒"];
  const choice = await vscode.window.showInformationMessage(
    `🎉 CodexAutoAI 有新版本 v${latest}（目前 v${current}）`, ...picks);
  if (choice === "下載 .vsix") {
    vscode.env.openExternal(vscode.Uri.parse(dlUrl));
    vscode.window.showInformationMessage(
      "下載 .vsix 後，在命令面板執行「Extensions: Install from VSIX…」或 `code --install-extension <檔>` 安裝。");
  } else if (choice === "查看 Release") {
    vscode.env.openExternal(vscode.Uri.parse(htmlUrl));
  } else if (choice === "不再提醒") {
    vscode.workspace.getConfiguration("codexautoai").update(
      "checkForUpdates", false, vscode.ConfigurationTarget.Global);
  }
}

function activate(context) {
  const extPath = context.extensionPath;

  // 啟動：把 full-auto 友善設定暫時套到全域 Claude/Codex；deactivate 時還原。
  // 預設開啟，可用設定 codexautoai.applyGlobalSettings 關掉。
  try {
    if (vscode.workspace.getConfiguration("codexautoai").get("applyGlobalSettings", true)) {
      overlayToken = `vscode:${process.pid}`;
      globalOverlay.acquire(overlayToken);
    }
  } catch (e) { console.warn("CodexAutoAI: 套用全域設定失敗：", e && e.message); }

  context.subscriptions.push(
    vscode.commands.registerCommand("codexautoai.init", async () => {
      const root = workspaceRoot();
      if (!root) { vscode.window.showErrorMessage("請先開啟一個資料夾。"); return; }
      if (hasFramework(root)) {
        vscode.window.showInformationMessage("CodexAutoAI 框架已存在於此資料夾。");
        return;
      }
      if (copyFramework(extPath, root)) {
        vscode.window.showInformationMessage("✓ CodexAutoAI 框架已放入此資料夾。下一步：執行「CodexAutoAI: 設定 / 修復」。");
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("codexautoai.setup", async () => {
      const root = workspaceRoot();
      if (!root) { vscode.window.showErrorMessage("請先開啟一個資料夾。"); return; }
      // 修復：缺漏框架檔照補；launcher（setup.ps1/cmd/sh）一律覆蓋成最新，避免舊專案留著沒 BOM/過期的壞檔。
      copyFramework(extPath, root, { force: ["setup.ps1", "setup.cmd", "setup.sh"] });

      // pre-check：本機若已安裝+登入 Claude / Codex / gh，就不重跑 setup（連終端機都不開）。
      // 可用設定 codexautoai.skipSetupWhenReady=false 關閉此偵測，永遠開終端機跑完整 setup。
      if (vscode.workspace.getConfiguration("codexautoai").get("skipSetupWhenReady", true)) {
        const checks = await vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: "CodexAutoAI：偵測環境…" },
          () => Promise.all([checkClaude(), checkCodex(), checkGh()]));
        const missing = checks.filter((c) => !c.ok);
        if (missing.length === 0) {
          vscode.window.showInformationMessage(
            "✓ 框架已就緒、環境已就緒：Claude / Codex / GitHub CLI 都已安裝並登入。可直接「啟動新任務」。");
          return;
        }
        vscode.window.showInformationMessage(
          "需要設定：" + missing.map((m) => `${m.name}（${m.msg}）`).join("、") + "；開啟終端機執行…");
      }

      const t = termInRoot(root, "CodexAutoAI Setup");
      t.show();
      // 用絕對路徑直接跑 setup.ps1（-NoProfile）：避免使用者 PowerShell profile 把 cwd 切到家目錄，
      // 也不依賴終端目前目錄；雙引號在 PowerShell 與 cmd host 都成立，setup.ps1 已是 UTF-8 BOM 故 5.1 也可跑。
      if (process.platform === "win32") {
        t.sendText(`powershell -NoProfile -ExecutionPolicy Bypass -File "${path.join(root, "setup.ps1")}"`);
      } else {
        t.sendText(`bash "${path.join(root, "setup.sh")}"`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("codexautoai.start", async () => {
      const root = workspaceRoot();
      if (!root) { vscode.window.showErrorMessage("請先開啟一個資料夾。"); return; }
      if (!hasFramework(root)) { copyFramework(extPath, root); }

      const cfg = vscode.workspace.getConfiguration("codexautoai");
      const req = await vscode.window.showInputBox({
        prompt: "你想做什麼？（CodexAutoAI 會自動跑完七階段）",
        value: cfg.get("defaultRequirement", ""),
        ignoreFocusOut: true,
      });
      if (req === undefined) return; // 取消

      const mode = await vscode.window.showQuickPick(
        [
          { label: "一般", detail: "互動式，Phase 2 會問你細節" },
          { label: "非停（autopilot）", detail: "連回合都不停，一路跑到交付（commit/push 仍會問）" },
        ],
        { placeHolder: "選擇執行模式" }
      );
      if (!mode) return;

      // 確保在專案資料夾執行（有些 PowerShell profile 啟動會把 cwd 切到家目錄）——
      // runClaudeInTerminal 內建 Set-Location 處理。
      runClaudeInTerminal(root, buildInner(req, mode.label.startsWith("非停")));
    })
  );

  // ── 控制台（webview 內嵌 GUI）：給不想碰 CLI/TUI 的使用者 ─────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("codexautoai.dashboard", () => {
      const root = workspaceRoot();
      if (!root) { vscode.window.showErrorMessage("請先開啟一個資料夾。"); return; }
      if (!hasFramework(root)) { copyFramework(extPath, root); }
      const cfg = vscode.workspace.getConfiguration("codexautoai");
      dashboard.openDashboard({
        vscode, root,
        defaultReq: cfg.get("defaultRequirement", ""),
        onStart: (requirement, autopilot, reply) => {
          if (!(requirement || "").trim()) { reply("請先輸入需求。"); return; }
          runClaudeInTerminal(root, buildInner(requirement, autopilot), { hidden: true });
          reply("✓ 已在背景啟動，下方進度會自動更新（terminal 隱藏，可按「顯示背景終端機」查看）。");
        },
        onSeed: (intent, autopilot, reply) => {
          if (!(intent || "").trim()) { reply("請先輸入要開發的功能意圖。"); return; }
          reply("產生 spec 中…");
          seedThenBuildInner(root, intent, cfg).then((r) => {
            if (!r.ok) {
              const tail = r.errors.length ? r.errors[r.errors.length - 1].detail : "";
              reply(`產生 spec 失敗：${tail.slice(0, 160)}`);
              return;
            }
            const safePath = r.specPath.replace(/"/g, "'");
            runClaudeInTerminal(root,
              buildInner(`依照規格檔 ${safePath} 開發，跑完整七階段`, autopilot), { hidden: true });
            reply(`✓ 已產生 spec（${r.specPath}）並在背景啟動。`);
          });
        },
        onShowTerminal: () => { if (lastTerminal) lastTerminal.show(); },
      });
    })
  );

  // ── 從 spec 開始開發（gs-spec-forge 整合，純附加）─────────────────────────
  // 先跑 `spec-forge seed "<意圖>"` 產出 spec.md（帶 gs-rag 檢索引用），再把該 spec 路徑
  // 當需求丟進「既有」start 流程的同一條 pipeline。不改動 codexautoai.start 的行為。
  context.subscriptions.push(
    vscode.commands.registerCommand("codexautoai.seedFromSpec", async () => {
      const root = workspaceRoot();
      if (!root) { vscode.window.showErrorMessage("請先開啟一個資料夾。"); return; }
      if (!hasFramework(root)) { copyFramework(extPath, root); }

      const cfg = vscode.workspace.getConfiguration("codexautoai");
      const intent = await vscode.window.showInputBox({
        prompt: "描述要開發的功能意圖（先產 spec 再跑 pipeline）",
        value: cfg.get("defaultRequirement", ""),
        ignoreFocusOut: true,
      });
      if (intent === undefined) return; // 取消

      // spec 產在 workspace 下的 vault/，讓 spec 與專案同處、可被 pipeline 讀到。
      const cands = specforge.candidates(cfg.get("specForgeCmd", "spec-forge"), extPath);
      const env = Object.assign({}, process.env, { SPEC_VAULT: path.join(root, "vault") });
      const safeIntent = (intent || "").replace(/"/g, "'");
      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "gs-spec-forge：產生 spec…" },
        () => specforge.trySeed(cands, safeIntent,
          { cwd: root, env, timeout: 60000, windowsHide: true }, exec));

      if (!result.ok) {
        // 全部候選都失敗：附上實際錯誤尾巴讓問題可診斷，並依情況給指引。
        const tail = result.errors.length
          ? result.errors[result.errors.length - 1].detail : "";
        const hint = result.errors.some((e) => /python/i.test(e.detail))
          ? "疑似找不到 Python——請先執行「CodexAutoAI: 安裝設定」。"
          : "內建輕量版應可直接用；若持續失敗請回報以下錯誤。";
        vscode.window.showErrorMessage(
          `gs-spec-forge: 產生 spec 失敗。${hint}${tail ? `（${tail.slice(0, 160)}）` : ""}`);
        return;
      }
      const specPath = result.specPath;

      // 交回既有 pipeline：以 spec 檔為依據跑七階段（沿用 start 的終端啟動慣例）。
      runClaudeInTerminal(root,
        `claude "依照規格檔 ${specPath.replace(/"/g, "'")} 開發，跑完整七階段"`);
      vscode.window.showInformationMessage(`✓ 已產生 spec：${specPath}，開始跑 pipeline。`);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("codexautoai.checkUpdate", () =>
      checkForUpdate(context, { manual: true }))
  );

  // 啟動時背景檢查（不阻塞 activate；失敗靜默）。
  checkForUpdate(context).catch(() => {});
}

function deactivate() {
  // 關閉：還原啟動時暫套的全域 Claude/Codex 設定（最後一個 owner 才真的還原）。
  try {
    if (overlayToken) { globalOverlay.release(overlayToken); overlayToken = null; }
  } catch (e) { console.warn("CodexAutoAI: 還原全域設定失敗：", e && e.message); }
}

module.exports = { activate, deactivate };
