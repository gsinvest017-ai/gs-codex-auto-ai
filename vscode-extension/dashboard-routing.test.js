const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const dashboard = require('./dashboard');
const routing = require('./routing');

test('Codex usage uses time-bounded cumulative deltas and never double counts reasoning', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(),'codex-usage-test-'));
  const dir = path.join(home,'.codex','sessions'); fs.mkdirSync(dir,{recursive:true});
  const root = path.join(home,'project');
  const records = [{type:'session_meta',payload:{cwd:root}}, ...[
    ['2026-09-08T00:00:00Z',1000,100,80],['2026-09-08T01:01:00Z',1030,107,85]
  ].map(([timestamp,input_tokens,output_tokens,reasoning_output_tokens]) => ({timestamp,type:'event_msg',payload:{type:'token_count',info:{total_token_usage:{input_tokens,output_tokens,reasoning_output_tokens,cached_input_tokens:0}}}}))];
  fs.writeFileSync(path.join(dir,'session.jsonl'),records.map(JSON.stringify).join('\n'));
  const orig = os.homedir; os.homedir = () => home;
  try {
    assert.deepEqual(dashboard.readCodexUsage(root,Date.parse('2026-09-08T01:00:00Z')), {sessions:1,inTok:30,outTok:7,cacheTok:0});
    assert.deepEqual(dashboard.readCodexUsage(root,Date.parse('2026-09-09T00:00:00Z')), {sessions:0,inTok:0,outTok:0,cacheTok:0});
  } finally {os.homedir=orig;}
});

test('routing editor saves Python-owned scenario and fallback settings using argv', async () => {
  let args;
  await routing.saveRoute('/workspace',{scenario:'review',provider:'claude',model:'model name',fallbackModel:'deepseek/test'},(exe,a,opts,done)=>{args=a;done(null,'{}');});
  assert.deepEqual(args.slice(1,10),['--save-route','--scenario','review','--provider','claude','--model','model name','--fallback-model','deepseek/test']);
  await assert.rejects(routing.saveRoute('/workspace',{scenario:'review',provider:'opencode'}), /主線/);
  await routing.saveRoute('/workspace',{scenario:'default',provider:'codex',model:''},(exe,a,opts,done)=>{args=a;done(null,'{}');});
  assert.equal(args.includes('--model'),false);
});

test('webview interactions reflect scenario edits, save payloads, preview and real evidence independently', () => {
  const elements = new Map(); const sent=[]; let listener;
  function elem(){return {value:'',textContent:'',innerHTML:'',className:'',style:{},children:[],appendChild(e){this.children.push(e);},replaceChildren(){this.children=[];}};}
  const document={getElementById(id){if(!elements.has(id))elements.set(id,elem());return elements.get(id);},createElement:elem};
  const html=dashboard.html('Review this change');
  const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(script,{document,acquireVsCodeApi:()=>({postMessage:m=>sent.push(m)}),window:{addEventListener:(name,fn)=>listener=fn}});
  const $=id=>document.getElementById(id);
  listener({data:{type:'catalog',catalog:{scenarios:[{name:'review',provider:'claude',model:'review-model'}],fallback:{model:'deepseek/test'}}}});
  $('scenario').value='review'; $('scenario').onchange();
  assert.match($('primaryNode').textContent,/claude/); assert.match($('secondaryNode').textContent,/codex/);
  $('provider').value='codex'; $('provider').onchange(); assert.equal($('routeModel').value,''); $('btnSaveRoute').onclick();
  assert.equal(sent.at(-1).selection.provider,'codex'); assert.equal(sent.at(-1).selection.fallbackModel,'deepseek/test');
  const beforeClear=sent.length; $('fallbackInput').value=''; $('btnSaveRoute').onclick(); assert.equal(sent.length,beforeClear); assert.match($('status').textContent,/未儲存/);
  listener({data:{type:'routePreview',route:{scenario:'review',provider:'codex',model:null,reason:'test'}}});
  assert.match($('routePreview').textContent,/未執行/);
  listener({data:{type:'state',exists:false,summary:{},routingStats:dashboard.summarizeRoutingAttempts([])}});
  assert.match($('evidenceStatus').textContent,/未驗證/);
  const stats=dashboard.summarizeRoutingAttempts([JSON.stringify({type:'model_attempt',run_id:'r',attempt_id:'a',actual_provider:'opencode',outcome:'ok',usage:{input_tokens:12,output_tokens:3}})]);
  listener({data:{type:'state',exists:false,summary:{},routingStats:stats}});
  assert.match($('evidenceStatus').textContent,/不一致/); assert.equal($('attemptRows').children.length,1);
  assert.match($('attemptRows').children[0].children[3].textContent,/12 \/ 3/);
  assert.match($('providerMetrics').textContent,/cache 未知/);
});

test('blank CLI model saves through real Python router and catalog remains aligned', async () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'codex-route-blank-'));
  await routing.saveRoute(root,{scenario:'review',provider:'claude',model:''});
  const catalog=await routing.getCatalog(root); const rule=catalog.scenarios.find((s)=>s.name==='review');
  assert.equal(rule.provider,'claude'); assert.equal(rule.model,null);
});


test('real runner record_attempt output reaches the webview with failed app status and all attempts', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'codex-failed-bridge-'));
  const fixture=require('../tests/fixtures/dashboard_failed_dispatcher.json');
  const {execFileSync}=require('child_process');
  const script="import json,sys; from pathlib import Path; from tools.codex_runner import record_attempt; data=json.loads(sys.stdin.buffer.read().decode('utf-8')); [record_attempt(Path(sys.argv[1]),e) for e in data]";
  execFileSync('python',['-c',script,root],{cwd:path.join(__dirname,'..'),input:JSON.stringify(fixture.events),encoding:'utf8'});
  fs.writeFileSync(path.join(root,'log','app-run.json'),JSON.stringify(fixture.run));
  assert.equal(fs.existsSync(path.join(root,'log','model-routing-events.jsonl')),false);
  let message; const bridge={html:'',postMessage(m){message=m;},onDidReceiveMessage(){return {dispose(){}};}};
  const originalHome=os.homedir; os.homedir=()=>{throw new Error('formal polling must not scan home history');};
  let dispose;
  try {
    dispose=dashboard.wireDashboard(bridge,{root});
    assert.equal(message.summary.historyLoaded,false);
    assert.equal(message.type,'state'); assert.equal(message.run.status,'failed'); assert.equal(message.summary.failed,true);
    assert.equal(message.routingStats.parent_run_id,fixture.run.run_id);
    assert.equal(message.routingStats.attempts.length,3); assert.ok(message.routingStats.attempts.every((a)=>a.outcome==='failed'));
    assert.match(message.summary.failureReason,/非 Git/); assert.match(message.summary.failureReason,/Not inside a trusted directory/);
    assert.equal(message.routingStats.providers.codex.inTok,null);
    const elements=new Map(); let listener;
    const elem=()=>({value:'',textContent:'',innerHTML:'',style:{},children:[],appendChild(e){this.children.push(e);},replaceChildren(){this.children=[];}});
    const document={getElementById(id){if(!elements.has(id))elements.set(id,elem());return elements.get(id);},createElement:elem};
    vm.runInNewContext(bridge.html.match(/<script>([\s\S]*?)<\/script>/)[1],{document,acquireVsCodeApi:()=>({postMessage(){}}),window:{addEventListener:(name,fn)=>listener=fn}});
    listener({data:message});
    assert.match(elements.get('phaseText').innerHTML,/失敗/); assert.doesNotMatch(elements.get('phaseText').innerHTML,/進行中/);
    assert.match(elements.get('failureReason').textContent,/非 Git/); assert.equal(elements.get('attemptRows').children.length,3);
    assert.equal(elements.get('codexCalls').textContent,'未載入');
    assert.match(elements.get('historyNotice').textContent,/不掃描/);
  } finally {os.homedir=originalHome;if(dispose)dispose();}
});


test('new app run never inherits an older phase7 or unscoped completion', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'codex-run-progress-'));
  fs.mkdirSync(path.join(root,'log'));
  const start=Date.parse('2026-09-08T09:00:00Z')/1000;
  fs.writeFileSync(path.join(root,'log','app-run.json'),JSON.stringify({run_id:'new-app',status:'running',started_at:start,updated_at:Date.now()/1000}));
  const old=[{event_type:'phase_end',phase:'phase7',status:'success',timestamp:'2026-09-08T08:59:59Z'}, {event_type:'phase_end',phase:'phase7',status:'success'},
    {event_type:'phase_end',phase:'phase7',status:'success',run_id:'other-current-run',timestamp:'2026-09-08T09:00:01Z'}];
  const eventPath=path.join(root,'log','events.jsonl'); fs.writeFileSync(eventPath,old.map(JSON.stringify).join('\n'));
  let state=dashboard.computeState(root,{includeHistory:false});
  assert.equal(state.summary.marker,0); assert.deepEqual(state.summary.completed,[]); assert.equal(state.summary.runStatus,'running');
  fs.appendFileSync(eventPath,'\n'+JSON.stringify({event_type:'phase_start',phase:'phase2',run_id:'new-app',ts:'2026-09-08T09:00:01Z'}));
  state=dashboard.computeState(root,{includeHistory:false}); assert.equal(state.summary.marker,2);
});

test('legacy exit-zero run is incomplete without delivery proof and keeps actual call metrics', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'codex-false-complete-')); fs.mkdirSync(path.join(root,'log'));
  const run={run_id:'legacy-app',status:'completed',started_at:1000};
  const runPath=path.join(root,'log','app-run.json'); const original=JSON.stringify(run); fs.writeFileSync(runPath,original);
  fs.writeFileSync(path.join(root,'log','events.jsonl'),JSON.stringify({type:'model_attempt',run_id:'invoke',parent_run_id:run.run_id,attempt_id:'a',outcome:'ok',actual_provider:'codex',usage:{input_tokens:328567,output_tokens:1769,cached_input_tokens:293504}}));
  let state=dashboard.computeState(root,{includeHistory:false});
  assert.equal(state.run.status,'incomplete'); assert.equal(state.summary.failed,true);
  assert.match(state.summary.failureReason,/Phase 7/); assert.equal(state.routingStats.providers.codex.inTok,328567);
  assert.equal(state.routingStats.providers.codex.outTok,1769); assert.equal(state.routingStats.providers.codex.cacheTok,293504);
  assert.equal(fs.readFileSync(runPath,'utf8'),original);
  const file=path.join(root,'log',`task-result-${run.run_id}.json`);
  fs.writeFileSync(file,JSON.stringify({schema_version:1,ended_at:1003,run_id:run.run_id,status:'completed',started_at:1001,completion_evidence:{run_id:'other-run',event_type:'phase_end',phase:7,status:'success',ts:new Date(1002000).toISOString()}}));
  state=dashboard.computeState(root,{includeHistory:false}); assert.equal(state.run.status,'incomplete');
  fs.writeFileSync(file,JSON.stringify({schema_version:1,ended_at:1003,run_id:run.run_id,status:'blocked',started_at:1001,reason:'CreateProcessAsUserW failed: 5'}));
  state=dashboard.computeState(root,{includeHistory:false}); assert.equal(state.run.status,'blocked'); assert.match(state.summary.failureReason,/CreateProcessAsUserW/);
});
