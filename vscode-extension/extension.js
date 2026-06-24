// CodexAutoAI VS Code extension — 啟動器（自帶框架快照）。
// 三個指令：初始化（把框架複製進 workspace）、啟動（輸入需求跑 claude）、設定/修復。
// 純 vscode API + Node fs，無第三方依賴。
const vscode = require("vscode");
const fs = require("fs");
const path = require("path");

function workspaceRoot() {
  const f = vscode.workspace.workspaceFolders;
  return f && f.length ? f[0].uri.fsPath : null;
}

function hasFramework(root) {
  return fs.existsSync(path.join(root, "CLAUDE.md")) &&
         fs.existsSync(path.join(root, ".claude"));
}

// 把 extension 自帶的 framework/ 快照複製進 workspace（已存在的不覆蓋）
function copyFramework(extPath, root) {
  const src = path.join(extPath, "framework");
  if (!fs.existsSync(src)) {
    vscode.window.showErrorMessage("CodexAutoAI: 找不到內建框架快照，請用 build-vsix 重新打包。");
    return false;
  }
  for (const entry of fs.readdirSync(src)) {
    const s = path.join(src, entry), d = path.join(root, entry);
    if (fs.existsSync(d)) continue; // 不覆蓋使用者既有檔
    fs.cpSync(s, d, { recursive: true });
  }
  return true;
}

function termInRoot(root, name) {
  return vscode.window.createTerminal({ name, cwd: root });
}

function activate(context) {
  const extPath = context.extensionPath;

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
      if (!hasFramework(root)) { copyFramework(extPath, root); }
      const t = termInRoot(root, "CodexAutoAI Setup");
      t.show();
      t.sendText(process.platform === "win32" ? "setup.cmd" : "bash setup.sh");
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

      const safe = (req || "").replace(/"/g, "'");
      const inner = mode.label.startsWith("非停")
        ? `claude "/autopilot on ${safe}"`
        : (safe ? `claude "${safe}"` : "claude");

      const t = termInRoot(root, "CodexAutoAI");
      t.show();
      t.sendText(inner);
    })
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
