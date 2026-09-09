const { currentRunEvent, validatedTaskResult } = require('./task-evidence');
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const { randomUUID } = require('crypto');

// The Python router owns classification; never duplicate its keyword rules here.
async function runRouter(root, args, execute = execFile) {
  const candidates = [path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'), 'python', 'python3'];
  const script = [path.join(__dirname, 'framework', 'tools', 'model_router.py'),
    path.join(__dirname, '..', 'tools', 'model_router.py'), path.join(root, 'tools', 'model_router.py')]
    .find((candidate) => fs.existsSync(candidate)) || path.join(root, 'tools', 'model_router.py');
  let last;
  for (const python of candidates) {
    try {
      const result = await new Promise((resolve, reject) => execute(python,
        [script, ...args, '--json', '--root', root],
        { cwd: root, timeout: 15000, windowsHide: true }, (error, stdout) => error ? reject(error) : resolve(stdout)));
      const route = JSON.parse(result);
      return route;
    } catch (error) { last = error; }
  }
  throw new Error(`無法取得模型路由：${last.message}`);
}

async function previewRoute(root, prompt, execute = execFile) {
  const route = await runRouter(root, ['--prompt', prompt], execute);
  if (!route.scenario || !route.provider || typeof route.reason !== 'string') throw new Error('Invalid routing response');
  return route;
}
async function applyPreset(root, preset, execute = execFile) {
  if (!['multi-provider', 'codex-first'].includes(preset)) throw new Error('未知路由模式');
  return runRouter(root, ['--preset', preset], execute);
}

async function getCatalog(root, execute = execFile) {
  return runRouter(root, ['--catalog'], execute);
}
async function saveRoute(root, selection, execute = execFile) {
  if (!selection || !['codex', 'claude'].includes(selection.provider)) throw new Error('主線僅允許 Codex 或 Claude');
  if (typeof selection.scenario !== 'string' || !selection.scenario) throw new Error('請選擇場景');
  const args = ['--save-route', '--scenario', selection.scenario, '--provider', selection.provider];
  if (String(selection.model || '').trim()) args.push('--model', String(selection.model).trim());
  if (Object.prototype.hasOwnProperty.call(selection, 'fallbackModel')) args.push('--fallback-model', String(selection.fallbackModel || '').trim());
  return runRouter(root, args, execute);
}

function createRun(root, prompt, route, now = () => Date.now() / 1000) {
  const log = path.join(root, 'log');
  fs.mkdirSync(log, { recursive: true });
  const staleAbort = path.join(log, 'abort.flag');
  if (fs.existsSync(staleAbort)) fs.renameSync(staleAbort, path.join(log, `abort-previous-${randomUUID()}.flag`));
  const file = path.join(log, 'app-run.json');
  const record = { source: 'vscode', run_id: randomUUID(), prompt, route, pid: process.pid, started_at: now(), status: 'running' };
  record.exit_file = path.join(log, `vscode-run-${record.run_id}.exit`);
  let stopped = false;
  const persist = () => fs.writeFileSync(file, JSON.stringify(record), 'utf8');
  const event = (type) => fs.appendFileSync(path.join(log, 'vscode-sessions.jsonl'), JSON.stringify({ ...record, type, timestamp: new Date().toISOString() }) + '\n', 'utf8');
  record.updated_at = now(); persist(); event('start');
  const owns = () => { try { return JSON.parse(fs.readFileSync(file, 'utf8')).run_id === record.run_id; } catch { return false; } };
  return {
    runId: record.run_id,
    record,
    exitFile: record.exit_file,
    heartbeat() { if (!stopped && owns()) { record.updated_at = now(); persist(); } },
    stop(status = 'stopped', reason = null) { if (stopped) return; stopped = true; record.status = status; record.reason = reason; record.updated_at = 0; record.ended_at = now(); if (owns()) persist(); event('end'); },
  };
}

function taskResult(root, run, exitCode = null) {
  let result;
  try { result = JSON.parse(fs.readFileSync(path.join(root, 'log', `task-result-${run.run_id}.json`), 'utf8')); } catch {}
  const validated = validatedTaskResult(result, run, exitCode);
  if (validated) return validated;
  return {status: exitCode !== null && exitCode !== 0 ? 'failed' : 'incomplete',
    reason: exitCode !== null && exitCode !== 0 ? `執行程序退出碼 ${exitCode}；請查看任務日誌。`
      : '模型呼叫已結束，但未取得本次任務的 Phase 7 完成交付證據。'};
}
module.exports = { previewRoute, applyPreset, getCatalog, saveRoute, createRun, currentRunEvent, taskResult };
