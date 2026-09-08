const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const routing = require('./routing');

test('router uses argv, retries Python, and preserves shell-like text without executing it', async () => {
  const calls = [];
  const prompt = '3D modeling $(do-not-run) & mesh';
  const route = await routing.previewRoute('/project with spaces', prompt, (exe, args, opts, done) => {
    calls.push({ exe, args, opts });
    if (calls.length === 1) return done(new Error('missing venv'));
    done(null, JSON.stringify({scenario:'3d_modeling',provider:'codex',model:'gpt-6-astra',reason:'3D'}));
  });
  assert.equal(route.model, 'gpt-6-astra');
  assert.equal(calls[1].args[2], prompt);
  assert.equal(calls[1].opts.shell, undefined);
});

test('run heartbeat and ownership prevent old terminal from clearing a newer task', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-routing-'));
  fs.mkdirSync(path.join(root,'log')); fs.writeFileSync(path.join(root,'log','abort.flag'),'previous abort');
  let now = 1000;
  const first = routing.createRun(root, 'first', null, () => now);
  assert.equal(fs.existsSync(path.join(root,'log','abort.flag')), false);
  assert.ok(fs.readdirSync(path.join(root,'log')).some((name) => name.startsWith('abort-previous-')));
  const second = routing.createRun(root, 'second', {provider:'codex'}, () => now);
  first.stop(); now = 1010; second.heartbeat();
  let record = JSON.parse(fs.readFileSync(path.join(root,'log','app-run.json')));
  assert.equal(record.prompt, 'second'); assert.equal(record.updated_at, 1010);
  second.stop('completed');
  record = JSON.parse(fs.readFileSync(path.join(root,'log','app-run.json')));
  assert.equal(record.updated_at, 0); assert.equal(record.status, 'completed');
  assert.equal(fs.readFileSync(path.join(root,'log','vscode-sessions.jsonl'),'utf8').trim().split('\n').length, 4);
});

test('extension launch arms guard before sendText and passes scoped routing context; refresh preserves project data', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-extension-'));
  const ext = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-bundle-'));
  fs.mkdirSync(path.join(ext,'framework','src','codexautoai_v2'),{recursive:true});
  fs.writeFileSync(path.join(ext,'framework','src','codexautoai_v2','version.py'),'NEW');
  fs.mkdirSync(path.join(root,'src'),{recursive:true}); fs.writeFileSync(path.join(root,'src','user.py'),'USER');
  fs.mkdirSync(path.join(root,'log')); fs.writeFileSync(path.join(root,'log','model-routing.json'),'CUSTOM');
  let options; let sends = 0; let failSend = false;
  const original = Module._load;
  Module._load = function(name, ...args) {
    if(name === 'vscode') return { window: { createTerminal(opts) { options = opts; return { show() {}, sendText() {
      if (failSend) throw new Error('terminal write failed');
      sends++; assert.equal(JSON.parse(fs.readFileSync(path.join(root,'log','app-run.json'))).status, 'running');
    } }; } } };
    return original.call(this,name,...args);
  };
  const extension = require('./extension'); Module._load = original;
  let tick; const originalInterval = global.setInterval;
  global.setInterval = (fn) => { tick = fn; return 12345; };
  try {
    const release = extension.reserveLaunch(root);
    assert.throws(() => extension.reserveLaunch(root), /本專案已有/); release();
    assert.equal(extension.refreshFrameworkCore(ext,root), true);
    assert.equal(fs.readFileSync(path.join(root,'src','user.py'),'utf8'),'USER');
    assert.equal(fs.readFileSync(path.join(root,'src','codexautoai_v2','version.py'),'utf8'),'NEW');
    assert.equal(fs.readFileSync(path.join(root,'log','model-routing.json'),'utf8'),'CUSTOM');
    extension.runClaudeInTerminal(root,'claude "spec"',{prompt:'建立3D模型',route:{provider:'codex',model:'gpt-6-astra'}});
    assert.equal(options.env.CODEXAUTOAI_TASK_PROMPT,'建立3D模型'); assert.ok(sends > 0);
    assert.equal(options.env.CODEXAUTOAI_PARENT_RUN_ID,JSON.parse(fs.readFileSync(path.join(root,'log','app-run.json'))).run_id);
    assert.match(extension.buildInner('build task',true), /python tools\/codex_runner.py --dispatcher --prompt "\/autopilot on build task" --cwd \./);
    assert.throws(() => extension.runClaudeInTerminal(root, 'claude'), /本專案已有/);
    assert.throws(() => extension.reserveLaunch(root), /本專案已有/);
    const events = path.join(root,'log','events.jsonl');
    fs.writeFileSync(events, JSON.stringify({event_type:'phase_end',phase:'phase7',status:'success',timestamp:'2000-01-01T00:00:00Z'}));
    tick(); assert.equal(JSON.parse(fs.readFileSync(path.join(root,'log','app-run.json'))).status,'running');
    fs.writeFileSync(events, JSON.stringify({event_type:'phase_end',phase:'phase7',status:'success',timestamp:new Date(Date.now()+1000).toISOString()}));
    tick(); assert.equal(JSON.parse(fs.readFileSync(path.join(root,'log','app-run.json'))).status,'completed');
    extension.reserveLaunch(root)();
    extension.runClaudeInTerminal(root, 'claude "next"');
    const failedRun = JSON.parse(fs.readFileSync(path.join(root,'log','app-run.json')));
    fs.writeFileSync(failedRun.exit_file, '2'); tick();
    assert.equal(JSON.parse(fs.readFileSync(path.join(root,'log','app-run.json'))).status,'failed');
    extension.reserveLaunch(root)();
    failSend = true;
    assert.throws(() => extension.runClaudeInTerminal(root, 'claude'), /terminal write failed/);
    assert.equal(JSON.parse(fs.readFileSync(path.join(root,'log','app-run.json'))).status,'launch_failed');
    extension.reserveLaunch(root)();
  } finally { global.setInterval = originalInterval; extension.deactivate(); }
  assert.equal(JSON.parse(fs.readFileSync(path.join(root,'log','app-run.json'))).updated_at, 0);
});


test('preset delegates config mutation to Python and rejects unknown choices', async () => {
  let args;
  const result = await routing.applyPreset('/workspace','multi-provider', (exe,a,opts,done) => {args=a; done(null,'{"preset":"multi-provider"}');});
  assert.equal(result.preset,'multi-provider'); assert.deepEqual(args.slice(1,3),['--preset','multi-provider']);
  await assert.rejects(routing.applyPreset('/workspace','unknown'), /未知/);
});
