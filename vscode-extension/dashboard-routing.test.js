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
