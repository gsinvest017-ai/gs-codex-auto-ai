// progressView.js — VS Code 側的 pipeline 進度面板 + 狀態列 + 中止按鈕。
//
// 在這之前，extension 只做一件事：開一個終端機。pipeline 跑起來後在 IDE 裡
// 看不到 phase、看不到花了多少、也沒有煞車，只能盯著終端機日誌。這個模組補上
// survey 講的「人類控場」缺口。
//
// 設計要點：
//   * **模型由 Python 算**。走 `python tools/events_model.py --json`，與 desktop
//     App 讀同一份模型——絕不在 JS 這邊重寫一份事件解析邏輯，否則兩邊 UI 早晚
//     會對「跑到哪」有不同說法。
//   * **靠 fs.watch 而非定時輪詢**。每 2 秒 spawn 一個 Python 行程在 Windows 上
//     又慢又吵；改成只在 events.jsonl 真的變動時才重算（100ms debounce）。
//     閒置時零行程。
//   * 純 vscode API + Node 內建模組，無第三方依賴（與 extension.js 一致）。
const vscode = require("vscode");
const fs = require("fs");
const path = require("path");
const { execFile } = require("child_process");

const POLL_DEBOUNCE_MS = 100;
// 沒有檔案變動時的保底重整間隔：watch 在某些網路磁碟 / WSL 掛載上不觸發。
const FALLBACK_REFRESH_MS = 15000;

// state → 圖示與文字。與 events_model.py 的 STATE_* 常數對應。
const STATE_LABEL = {
  not_started: "尚未開始",
  running: "進行中",
  escalated: "失敗 / 升級",
  done: "完成",
};

function pythonExe(root) {
  // 與 CLAUDE.md 的環境約定一致：優先用專案 venv，其次 PATH。
  const venv = process.platform === "win32"
    ? path.join(root, ".venv", "Scripts", "python.exe")
    : path.join(root, ".venv", "bin", "python");
  if (fs.existsSync(venv)) return venv;
  return process.platform === "win32" ? "python" : "python3";
}

// 跑 events_model.py --json 取模型。失敗一律回 null（面板降級，不彈錯誤視窗）。
function readModel(root) {
  return new Promise((resolve) => {
    const script = path.join(root, "tools", "events_model.py");
    if (!fs.existsSync(script)) { resolve(null); return; }
    execFile(
      pythonExe(root),
      [script, "--json", "--log", path.join(root, "log", "events.jsonl")],
      { timeout: 10000, windowsHide: true, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err) { resolve(null); return; }
        try { resolve(JSON.parse(stdout)); } catch (_) { resolve(null); }
      }
    );
  });
}

// phase state → ThemeIcon。用內建圖示，避免打包額外資源。
function phaseIcon(state) {
  switch (state) {
    case "done": return new vscode.ThemeIcon("pass-filled", new vscode.ThemeColor("charts.green"));
    case "active": return new vscode.ThemeIcon("sync~spin", new vscode.ThemeColor("charts.yellow"));
    case "failed": return new vscode.ThemeIcon("error", new vscode.ThemeColor("charts.red"));
    default: return new vscode.ThemeIcon("circle-outline");
  }
}

class ProgressProvider {
  constructor(root) {
    this.root = root;
    this.model = null;
    this._emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._emitter.event;
  }

  async refresh() {
    this.model = this.root ? await readModel(this.root) : null;
    this._emitter.fire();
    return this.model;
  }

  getTreeItem(item) { return item; }

  getChildren() {
    const m = this.model;
    if (!m || !m.log_exists) {
      const hint = new vscode.TreeItem("尚未開始 — 執行「CodexAutoAI: 啟動」");
      hint.iconPath = new vscode.ThemeIcon("rocket");
      hint.command = { command: "codexautoai.start", title: "啟動" };
      return [hint];
    }

    const items = m.phases.map((p) => {
      const it = new vscode.TreeItem(`Phase ${p.num} · ${p.name}`);
      it.iconPath = phaseIcon(p.state);
      it.description = { done: "完成", active: "進行中", failed: "失敗" }[p.state] || "";
      return it;
    });

    // 迴圈 / 成本 / 錯誤：只在有值時才顯示，避免面板長期掛著 0。
    if (m.iteration) {
      const it = new vscode.TreeItem(`修復迭代：第 ${m.iteration} 輪`);
      it.iconPath = new vscode.ThemeIcon("debug-restart");
      items.push(it);
    }
    if (m.cost_usd) {
      const it = new vscode.TreeItem(`累計成本：$${Number(m.cost_usd).toFixed(4)}`);
      it.iconPath = new vscode.ThemeIcon("credit-card");
      items.push(it);
    }
    if (m.errors && m.errors.length) {
      const last = m.errors[m.errors.length - 1];
      const it = new vscode.TreeItem(`最後錯誤：${last.reason}`);
      it.description = last.phase || "";
      it.tooltip = `${last.phase || ""} @ ${last.timestamp || ""}\n${last.reason}`;
      it.iconPath = new vscode.ThemeIcon("warning", new vscode.ThemeColor("charts.red"));
      items.push(it);
    }
    return items;
  }
}

// 放下中止旗標。**只在回合邊界生效**——殺不掉正在跑的 codex 子行程，
// 所以文案不能寫「立即中止」（對應 tools/autopilot/cont.py 的閥 4）。
async function abortPipeline(root) {
  if (!root) { vscode.window.showErrorMessage("請先開啟一個資料夾。"); return; }
  const pick = await vscode.window.showWarningMessage(
    "要中止目前的 CodexAutoAI pipeline 嗎？",
    { modal: true, detail: "會在下一個回合邊界停止；正在執行的 Codex 無法立即中斷。" },
    "中止"
  );
  if (pick !== "中止") return;
  try {
    const dir = path.join(root, "log");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "abort.flag"), "", "utf8");
    vscode.window.showInformationMessage("已送出中止，將於下一個回合邊界停止。");
  } catch (e) {
    vscode.window.showErrorMessage(`寫入中止旗標失敗：${(e && e.message) || e}`);
  }
}

// 把 TreeView + StatusBar + watcher 全部裝起來，回傳 disposable 陣列。
function register(context, root) {
  const provider = new ProgressProvider(root);
  const view = vscode.window.createTreeView("codexautoaiProgress", {
    treeDataProvider: provider,
  });

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  status.command = "codexautoai.showProgress";
  status.tooltip = "CodexAutoAI pipeline 進度";

  const paint = (m) => {
    if (!m || !m.log_exists) {
      status.text = "$(rocket) CodexAutoAI";
      view.description = undefined;
      status.hide();
      return;
    }
    const icon = { running: "$(sync~spin)", done: "$(pass-filled)",
                   escalated: "$(error)" }[m.state] || "$(circle-outline)";
    const cost = m.cost_usd ? ` · $${Number(m.cost_usd).toFixed(2)}` : "";
    status.text = `${icon} CodexAutoAI P${m.marker}/${m.total}${cost}`;
    status.tooltip = `Phase ${m.marker}/${m.total} ${m.current_name} — ${STATE_LABEL[m.state] || m.state}`;
    view.description = `P${m.marker}/${m.total} ${m.current_name}`;
    status.show();
  };

  let timer = null;
  const schedule = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { provider.refresh().then(paint); }, POLL_DEBOUNCE_MS);
  };

  // events.jsonl 由 pipeline append。用 workspace watcher（跨平台、含 WSL 掛載）。
  let watcher = null;
  if (root) {
    watcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(root, "log/events.jsonl"));
    watcher.onDidChange(schedule);
    watcher.onDidCreate(schedule);
    watcher.onDidDelete(schedule);
  }

  // 保底重整：watcher 在部分掛載點不觸發時仍能更新。
  const interval = setInterval(schedule, FALLBACK_REFRESH_MS);

  provider.refresh().then(paint);

  const disposables = [
    view,
    status,
    { dispose: () => { clearInterval(interval); if (timer) clearTimeout(timer); } },
    vscode.commands.registerCommand("codexautoai.refreshProgress", () =>
      provider.refresh().then(paint)),
    vscode.commands.registerCommand("codexautoai.showProgress", async () => {
      await vscode.commands.executeCommand("workbench.view.explorer");
      const m = await provider.refresh();
      paint(m);
      if (m && m.log_exists) view.reveal(undefined, { focus: true }).then(() => {}, () => {});
    }),
    vscode.commands.registerCommand("codexautoai.abort", () => abortPipeline(root)),
  ];
  if (watcher) disposables.push(watcher);
  context.subscriptions.push(...disposables);
  return { provider, refresh: schedule };
}

module.exports = { register, readModel, ProgressProvider, abortPipeline };
