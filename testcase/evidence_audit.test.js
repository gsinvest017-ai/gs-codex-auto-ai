const test=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),os=require('os'),path=require('path');
const {audit,auditMatrix,stageChecks,render,historyChecks}=require('./evidence_audit');
function fixture(){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'evidence-audit-'));fs.mkdirSync(path.join(root,'log'));fs.mkdirSync(path.join(root,'output'));
 const run={run_id:'current',prompt:'建立 3D 模型',route:{scenario:'3d',provider:'codex',model:null},started_at:100,status:'completed'};
 fs.writeFileSync(path.join(root,'log/app-run.json'),JSON.stringify(run));
 const events=[{event_type:'phase_end',run_id:'old',ts:new Date(150000).toISOString(),phase:7,status:'success'},
 {type:'model_attempt',run_id:'old',attempt_id:'old:a',actual_provider:'codex',outcome:'ok',usage:{input_tokens:999999}},
 {type:'model_attempt',run_id:'current',attempt_id:'current:a',actual_provider:'codex',actual_model:null,outcome:'ok',result_path:'log/provider.jsonl',usage:{input_tokens:10,output_tokens:3,cached_input_tokens:null}},
 {event_type:'phase_start',run_id:'current',ts:new Date(101000).toISOString(),phase:2}];
 fs.writeFileSync(path.join(root,'log/events.jsonl'),events.map(JSON.stringify).join('\n'));
 fs.writeFileSync(path.join(root,'log/provider.jsonl'),[
 {type:'item.started',item:{id:'cmd',type:'command_execution',command:'python build.py',status:'in_progress'}},
 {type:'item.completed',item:{id:'cmd',type:'command_execution',command:'python build.py',status:'completed'}},
 {type:'item.completed',item:{id:'private',type:'reasoning',text:'MUST_NOT_APPEAR'}},
 {type:'item.completed',item:{id:'message',type:'agent_message',text:'模型已產生。'}}
 ].map(JSON.stringify).join('\n'));
 fs.writeFileSync(path.join(root,'log/task-result-old.json'),JSON.stringify({schema_version:1,run_id:'old',status:'blocked',started_at:1,ended_at:90}));
 const json=Buffer.from(JSON.stringify({asset:{version:'2.0'},scene:0,scenes:[{nodes:[]}]}).padEnd(80,' '));
 const glb=Buffer.alloc(20+json.length);glb.writeUInt32LE(0x46546c67);glb.writeUInt32LE(2,4);glb.writeUInt32LE(glb.length,8);glb.writeUInt32LE(json.length,12);glb.writeUInt32LE(0x4e4f534a,16);json.copy(glb,20);fs.writeFileSync(path.join(root,'output/model.glb'),glb);
 return root;
}
test('production Python/JS audit isolates stale runs, unknown model, CLI success without delivery, historical blocked, editable prompt and disabled autopilot',async()=>{
 const result=await audit(fixture(),{autopilot:false,edited_prompt:'尚未送出另一需求',assert:{'run.status':'incomplete','run.route.scenario':'3d','metrics.providers.codex.inTok':10,'metrics.attempts.0.actual_model':null,'history.runs.0.task_status':'blocked','ui.startMessage.autopilot':false,'ui.editedPrompt':'尚未送出另一需求'}});
 assert.equal(result.summary.failed,0,JSON.stringify(result.checks.filter(c=>c.status==='failed')));
 assert.equal(result.observations.models.length,1);
 assert.ok(result.checks.some(c=>c.id==='runtime_continuation_mode' && c.status==='unknown'));
 assert.equal(result.observations.metrics.attempts.length,1);
 assert.equal(result.observations.activity.items.length,2);
 assert.doesNotMatch(JSON.stringify(result.observations.activity),/MUST_NOT_APPEAR|in_progress/);
 assert.equal(result.observations.ui.activityCount,2);
 assert.equal(result.observations.summary.completed.includes(7),false);
});
test('audit detects expected route/run/usage mismatch instead of fabricating UI success',async()=>{
 const result=await audit(fixture(),{autopilot:true,assert:{'run.run_id':'wrong','run.route.provider':'claude','metrics.providers.codex.inTok':0}});
 assert.equal(result.summary.failed,3);assert.equal(result.observations.ui.startMessage.autopilot,true);
});
test('matrix parity rejects missing evidence and detects altered canonical metrics',()=>{
 const empty=require('../vscode-extension/dashboard').summarizeRoutingAttempts([],'case');
 const report={schema_version:1,dimensions:{nonstop:[false]},quota_outcomes:['ok'],coverage:{parameter_combinations:1,execution_combinations:1},cases:[{id:'case',parameters:{nonstop:false},representatives:{prompt:'request'},checks:[{status:'passed'}],executions:[{run_id:'case',outcome:'ok',events:[],canonical_metrics:empty,checks:[{status:'passed'}]}]}]};
 assert.equal(auditMatrix(report).summary.failed,0);
 report.cases[0].executions[0].canonical_metrics={...empty,status:'fabricated'};
 assert.equal(auditMatrix(report).summary.failed,1);
 delete report.cases[0].executions[0].canonical_metrics;
 assert.throws(()=>auditMatrix(report),/canonical execution evidence/);
 assert.throws(()=>auditMatrix({schema_version:1,cases:[]}));
});
function validMatrix(){return {schema_version:1,dimensions:{nonstop:[false,true]},quota_outcomes:['ok'],coverage:{parameter_combinations:2,execution_combinations:2},cases:[false,true].map((nonstop,i)=>({id:'case'+i,parameters:{nonstop},representatives:{prompt:'request'},checks:[{status:'passed'}],executions:[{run_id:'run'+i,outcome:'ok',events:[],canonical_metrics:require('../vscode-extension/dashboard').summarizeRoutingAttempts([],'run'+i),checks:[{status:'passed'}]}]}))};}
test('matrix rejects truncated, duplicate, unknown evidence and propagates source failures',()=>{
 for(const mutate of [r=>r.cases[0].executions=[],r=>r.cases[1].id=r.cases[0].id,r=>r.cases[1].parameters=r.cases[0].parameters,r=>r.coverage.execution_combinations=99,r=>r.cases[0].checks[0].status='unknown',r=>delete r.cases[0].executions[0].canonical_metrics,r=>r.cases[1].executions[0].run_id='run0']){const r=validMatrix();mutate(r);assert.throws(()=>auditMatrix(r));}
 const r=validMatrix();assert.equal(auditMatrix(r).summary.failed,0);r.cases[0].checks[0].status='failed';assert.equal(auditMatrix(r).summary.failed,1);
});

test('matrix refuses a passed label that contradicts expected versus actual',()=>{
 for(const target of ['case','execution']){const report=validMatrix();const checks=target==='case'?report.cases[0].checks:report.cases[0].executions[0].checks;checks.push({status:'passed',expected:{provider:'opencode'},actual:{provider:'codex'}});assert.throws(()=>auditMatrix(report),/contradicts/);}
});

test('new explicit presets accept actual Python metrics including OpenCode primary without a false quota warning',()=>{
 const presets=['claude-first','opencode-first','review-codex-build-claude'];
 const report={schema_version:1,dimensions:{nonstop:[false],preset:presets},quota_outcomes:['primaryok'],coverage:{parameter_combinations:3,execution_combinations:3},cases:presets.map((preset,i)=>{
  const run_id='explicit-'+i,provider=preset==='opencode-first'?'opencode':'claude';
  const events=[{type:'model_attempt',run_id,attempt_id:run_id+':1',actual_provider:provider,configured_model:provider==='opencode'?'vendor/model':null,actual_model:null,routing_policy:'explicit-primary',config_digest:'a'.repeat(64),outcome:'ok',usage:{input_tokens:11,output_tokens:3}}];
  const python='import json,sys;from tools.events_model import routing_stats;e=json.load(sys.stdin);print(json.dumps(routing_stats(e,run_id=e[0]["run_id"])))';
  const canonical_metrics=JSON.parse(require('child_process').execFileSync('python',['-c',python],{cwd:path.resolve(__dirname,'..'),input:JSON.stringify(events),encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}}));
  assert.deepEqual(canonical_metrics.violations,[]);
  return {id:preset,parameters:{nonstop:false,preset},representatives:{prompt:'Controlled request'},checks:[{status:'passed',expected:provider,actual:provider}],executions:[{run_id,outcome:'primaryok',events,canonical_metrics,checks:[{status:'passed',expected:11,actual:11}]}]};})};
 const result=auditMatrix(report);assert.equal(result.summary.failed,0);assert.equal(result.summary.passed,7);assert.equal(result.summary.unknown,1);
 report.cases[1].executions[0].events[0].routing_policy='quota-only';
 assert.equal(auditMatrix(report).summary.failed,1);
});
test('stage audit observes rendered DOM and detects corrupt number, bar and completion label',()=>{
 const dashboard=require('../vscode-extension/dashboard'),state=dashboard.computeState(fixture(),{includeHistory:false});
 const ui=render(state,{items:[]},{items:[]},{runs:[]});
 assert.equal(stageChecks(state,ui).filter(c=>c.status==='failed').length,0);
 const polluted={...ui,phaseText:ui.phaseText.replace(/^Phase \d+/,'Phase 7').replace(/<span[^>]*>.*?<\/span>/,'<span>✓ 任務已完成</span>'),progressBar:'▓▓▓▓▓▓▓▓'};
 assert.equal(stageChecks(state,polluted).filter(c=>c.status==='failed').length,3);
});


test('empty history placeholder is not a task row, and missing or forged rows remain audit failures',()=>{
 const dashboard=require('../vscode-extension/dashboard'),state=dashboard.computeState(fixture(),{includeHistory:false});
 const history={runs:[]},ui=render(state,{items:[]},{items:[]},history);
 assert.equal(ui.historyChildCount,1);assert.equal(ui.historyCount,0);assert.deepEqual(ui.historyRunIds,[]);
 assert.equal(historyChecks(history,ui).filter(c=>c.status==='failed').length,0);
 assert.equal(historyChecks(history,{...ui,historyPlaceholders:[]}).filter(c=>c.status==='failed').length,1);
 const populated={runs:[{run_id:'expected-run'}]};
 assert.ok(historyChecks(populated,ui).some(c=>c.status==='failed'));
 assert.ok(historyChecks(history,{...ui,historyCount:1,historyRunIds:['invented-run']}).some(c=>c.status==='failed'));
});

test('real Python audit with only current run accepts empty history and preserves Chinese activity',async()=>{
 const root=fixture(),file=path.join(root,'log/events.jsonl');
 const lines=fs.readFileSync(file,'utf8').split(/\n/).filter(line=>JSON.parse(line).run_id==='current');fs.writeFileSync(file,lines.join('\n'));
 const result=await audit(root);
 assert.equal(result.summary.failed,0,JSON.stringify(result.checks.filter(c=>c.status==='failed')));
 assert.equal(result.observations.history.runs.length,0);assert.equal(result.observations.ui.historyCount,0);assert.equal(result.observations.ui.historyChildCount,1);
 assert.ok(result.observations.activity.items.some(item=>item.text==='模型已產生。'));
});
