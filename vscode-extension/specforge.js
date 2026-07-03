// specforge.js — 解析「從 spec 開始」要用哪個 spec-forge，回傳依序嘗試的候選清單。
// 獨立於 vscode API（純 Node），extension.js require 使用；也讓此邏輯可被直接單元測試。
//
// 候選順序（前者失敗自動落到下一個）：
//   1. configured — 使用者明確設定 codexautoai.specForgeCmd（非預設值才算）
//   2. venv       — 標準安裝位置 ~/gs-spec-forge/.venv（install-spec-forge 產物）
//   3. path       — PATH 上的裸 spec-forge
//   4. bundled    — 內建快照（.vsix 隨附的 stdlib-first 核心，python -m 執行）
//                   讓「沒裝 gh / 沒 repo 權限 / 沒裝 gs-spec-forge」的使用者開箱即用。
const fs = require("fs");
const os = require("os");
const path = require("path");

// 每個候選：{ kind, buildCmd(args) -> 完整命令字串, env -> 額外環境變數 }
// args 一律由呼叫端先做過引號消毒（把 " 換成 '）。
function candidates(configured, extPath) {
  const list = [];
  const q = (s) => `"${s}"`;

  if (configured && configured !== "spec-forge") {
    list.push({ kind: "configured", buildCmd: (a) => `${q(configured)} ${a}`, env: {} });
  }

  const venv = process.platform === "win32"
    ? path.join(os.homedir(), "gs-spec-forge", ".venv", "Scripts", "spec-forge.exe")
    : path.join(os.homedir(), "gs-spec-forge", ".venv", "bin", "spec-forge");
  if (fs.existsSync(venv)) {
    list.push({ kind: "venv", buildCmd: (a) => `${q(venv)} ${a}`, env: {} });
  }

  list.push({ kind: "path", buildCmd: (a) => `spec-forge ${a}`, env: {} });

  const snap = path.join(extPath, "spec_forge_snapshot");
  if (fs.existsSync(path.join(snap, "gs_spec_forge", "cli.py"))) {
    const env = {
      PYTHONPATH: snap + path.delimiter + (process.env.PYTHONPATH || ""),
      PYTHONIOENCODING: "utf-8", // 保險：即使 cli 的 _force_utf8_io 失效也維持 UTF-8 邊界
    };
    list.push({ kind: "bundled", buildCmd: (a) => `python -m gs_spec_forge.cli ${a}`, env });
    if (process.platform === "win32") {
      // WindowsApps 的 python 假蓋子未安裝時會失敗；再備一個 py launcher 候選。
      list.push({ kind: "bundled-py", buildCmd: (a) => `py -3 -m gs_spec_forge.cli ${a}`, env });
    }
  }
  return list;
}

// 依序嘗試候選執行 seed。execFn 簽名同 child_process.exec(cmd, opts, cb)。
// 成功條件：exit 0 且 stdout 末行是 .md 路徑。回傳 Promise<{ok, specPath?, kind?, errors[]}>
function trySeed(cands, intentArg, baseOpts, execFn) {
  const errors = [];
  const attempt = (i) => {
    if (i >= cands.length) return Promise.resolve({ ok: false, errors });
    const c = cands[i];
    const opts = Object.assign({}, baseOpts, {
      env: Object.assign({}, baseOpts.env || {}, c.env),
    });
    return new Promise((resolve) => {
      execFn(c.buildCmd(`seed "${intentArg}"`), opts, (err, stdout, stderr) => {
        const out = (stdout || "").trim();
        const last = out ? out.split(/\r?\n/).pop().trim() : "";
        if (!err && last.toLowerCase().endsWith(".md")) {
          resolve({ ok: true, specPath: last, kind: c.kind, errors });
        } else {
          errors.push({ kind: c.kind, detail: ((stderr || "") + (err ? ` [${err.message}]` : "")).trim().slice(0, 200) });
          resolve(attempt(i + 1));
        }
      });
    });
  };
  return attempt(0);
}

module.exports = { candidates, trySeed };
