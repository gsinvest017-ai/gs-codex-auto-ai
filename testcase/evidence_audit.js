// Read-only evidence audit: real production parsers and Python Workbench, no model calls.
const fs=require('fs'),path=require('path'),vm=require('vm');
const {execFile}=require('child_process');
const {promisify,isDeepStrictEqual}=require('util');
const dashboard=require('../vscode-extension/dashboard');
const preview=require('../vscode-extension/preview3d');
const execute=promisify(execFile);
function buildCommand(prompt,autopilot){const Module=require('module'),original=Module._load;let extension;try{Module._load=function(name,...args){return name==='vscode'?{}:original.call(this,name,...args);};extension=require('../vscode-extension/extension');}finally{Module._load=original;}return extension.buildInner(prompt,autopilot);}
function render(state,activity,artifacts,history,edit='尚未送出的編輯',autopilot=false){
 const elements=new Map(),messages=[];let listener;
 const element=()=>({value:'',checked:false,textContent:'',innerHTML:'',className:'',style:{},children:[],appendChild(e){this.children.push(e);},replaceChildren(){this.children=[];}});
 const document={getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:element};
 vm.runInNewContext(dashboard.html('').match(/<script>([\s\S]*?)<\/script>/)[1],{document,acquireVsCodeApi:()=>({postMessage:m=>messages.push(m)}),window:{addEventListener:(n,fn)=>listener=fn}});
 const $=id=>document.getElementById(id);$('req').value=edit;$('autopilot').checked=autopilot;
 for(const data of [{type:'state',...state},{type:'activity',activity},{type:'artifacts',result:artifacts},{type:'history',history}])listener({data});
 $('btnStart').onclick();
 return {phaseText:$('phaseText').innerHTML || $('phaseText').textContent,progressBar:$('bar').textContent,editedPrompt:$('req').value,executedRequirement:$('executedRequirement').textContent,runState:$('runState').textContent,startMessage:JSON.parse(JSON.stringify(messages.at(-1))),activityCount:$('activityTimeline').children.length,artifactCount:$('artifactList').children.length,historyCount:$('projectHistoryList').children.filter(row=>typeof row.title==='string' && row.title.length>0).length,historyRunIds:$('projectHistoryList').children.filter(row=>typeof row.title==='string' && row.title.length>0).map(row=>row.title),historyPlaceholders:$('projectHistoryList').children.filter(row=>!row.title).map(row=>row.textContent),historyChildCount:$('projectHistoryList').children.length};
}
function historyChecks(history,ui){
 const checks=[],check=(id,actual,expected)=>checks.push({id,status:isDeepStrictEqual(actual,expected)?'passed':'failed',expected,actual});
 const ids=history.runs.map(run=>run.run_id);
 check('history_rows_visible',ui.historyCount,ids.length);
 check('history_row_identities',ui.historyRunIds,ids);
 check('history_empty_placeholder',ui.historyPlaceholders,ids.length?[]:['沒有其他可歸屬的歷史任務；不將缺少紀錄視為零用量。']);
 return checks;
}
function stageChecks(state,ui){
 const checks=[],check=(id,actual,expected)=>checks.push({id,status:isDeepStrictEqual(actual,expected)?'passed':'failed',expected,actual});
 if(!state.exists){check('stage_not_started',ui.phaseText.includes('尚未開始'),true);return checks;}
 const s=state.summary,marker=s.marker || 0;
 check('stage_number',Number((ui.phaseText.match(/^Phase (\d+)\/7 /) || [])[1]),marker);
 check('stage_progress_bar',ui.progressBar,'▓'.repeat(marker+1)+'░'.repeat(7-marker));
 const labels={completed:'✓ 任務已完成',blocked:'⚠ 任務受阻',incomplete:'⚠ 已停止，任務未完成',stopped:'■ 任務已停止','心跳逾期':'⚠ 心跳逾期，狀態未知'};
 const expected=labels[s.runStatus] || (s.failed?'✗ 失敗/升級':marker===7 && [...s.completed,...s.started].includes(7)?'✓ 交付階段':'● 進行中');
 check('stage_status_label',(ui.phaseText.match(/<span[^>]*>(.*?)<\/span>/) || [])[1],expected);
 return checks;
}
async function audit(root,expected={}){
 root=path.resolve(root);const checks=[];
 const check=(id,actual,wanted)=>checks.push({id,status:isDeepStrictEqual(actual,wanted)?'passed':'failed',expected:wanted,actual});
 const wb=async(action)=>{const {stdout}=await execute(process.env.PYTHON || 'python',[path.resolve(__dirname,'../tools/workbench.py'),'--root',root,'--action',action],{encoding:'utf8',env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'},maxBuffer:16*1024*1024});return JSON.parse(stdout);};
 const [status,activity,artifacts,history]=await Promise.all([wb('status'),wb('activity'),wb('artifacts'),dashboard.loadProjectHistory(root,JSON.parse(fs.readFileSync(path.join(root,'log/app-run.json'),'utf8')).run_id)]);
 const state=dashboard.computeState(root,{includeHistory:false});
 check('run_scope',state.run.run_id,status.run_id);
 check('activity_scope',activity.run_id,status.run_id);
 check('full_metrics_python_javascript',state.routingStats,status.metrics);
 check('history_excludes_current',history.runs.some(r=>r.run_id===state.run.run_id),false);
 check('activity_attempt_scope',activity.items.every(a=>status.attempts.some(t=>t.attempt_id===a.attempt_id) || a.kind==='progress'),true);
 if(['completed','blocked','incomplete','failed'].includes(status.task_status))check('task_delivery_status',state.run.status,status.task_status);
 else checks.push({id:'task_delivery_status',status:'unknown',actual:status.task_status,reason:'No validated task-result envelope; CLI success does not prove delivery.'});
 const ui=render(state,activity,artifacts,history,expected.edited_prompt ?? '尚未送出的編輯',expected.autopilot ?? false);
 checks.push(...stageChecks(state,ui));
 check('prompt_edit_preserved',ui.editedPrompt,expected.edited_prompt ?? '尚未送出的編輯');
 check('start_autopilot_payload',ui.startMessage.autopilot,expected.autopilot ?? false);
 const command=buildCommand(ui.startMessage.requirement,ui.startMessage.autopilot);
 check('requested_mode_command_prefix',command.includes('--prompt "/autopilot on '),expected.autopilot ?? false);
 checks.push({id:'persisted_mode_evidence',status:'unknown',reason:'app-run/attempt do not persist an authoritative runtime continuation mode snapshot.'});
 checks.push({id:'runtime_continuation_mode',status:'unknown',reason:'Only the requested UI bool is verified; this read-only audit does not execute or prove runtime continuation.'});
 check('executed_prompt_source',ui.executedRequirement,state.run.prompt?'本次執行原需求：'+String(state.run.prompt).slice(0,500)+(state.run.prompt.length>500?'…':''):'尚無本次執行的原需求紀錄。');
 check('planned_route_display',ui.runState,'任務狀態（預定路由）：'+state.run.status+(state.run.route?'・'+state.run.route.scenario+' → '+state.run.route.provider+' / '+(state.run.route.model || 'CLI 預設'):''));
 check('artifact_list_visible',ui.artifactCount,Math.min(artifacts.items.length,100));
 checks.push(...historyChecks(history,ui));
 check('recent_activity_visible',ui.activityCount,Math.min(activity.items.length,5));
 const models=[];
 for(const a of artifacts.items.filter(a=>/^(glb|gltf)$/i.test(a.format))){
  try{const model=preview.readModel(root,a.relative_path);check('preview_path:'+a.relative_path,model.name.replace(/\\/g,'/'),a.relative_path.replace(/\\/g,'/'));check('preview_gltf_version:'+a.relative_path,model.json.asset.version,'2.0');models.push({path:a.relative_path,signature:model.signature});}
  catch(error){checks.push({id:'preview:'+a.relative_path,status:'failed',actual:error.message});}
 }
 if(!models.length)checks.push({id:'model_preview',status:'unknown',reason:'No readable GLB/glTF artifact in this project.'});
 const observations={run:state.run,summary:state.summary,metrics:state.routingStats,activity,artifacts,history,ui,models,command};
 for(const [key,wanted]of Object.entries(expected.assert || {})){let value=observations;for(const part of key.split('.'))value=value?.[part];check('expected:'+key,value,wanted);}
 return {schema_version:1,root,source:'production_dashboard_and_workbench_cli_readonly',checks,summary:Object.fromEntries(['passed','failed','unknown'].map(s=>[s,checks.filter(c=>c.status===s).length])),observations};
}
function auditMatrix(report){
 if(report.schema_version!==1 || !Array.isArray(report.cases) || !report.cases.length)throw Error('Missing versioned matrix cases');
 const dimensions=report.dimensions,outcomes=report.quota_outcomes;
 if(!dimensions || !Object.keys(dimensions).length || !Array.isArray(outcomes) || !outcomes.length || new Set(outcomes).size!==outcomes.length)throw Error('Missing or invalid matrix manifest dimensions/outcomes');
 let expectedCount=1;
 for(const values of Object.values(dimensions)){if(!Array.isArray(values)||!values.length||new Set(values.map(JSON.stringify)).size!==values.length)throw Error('Invalid matrix dimension');expectedCount*=values.length;}
 if(report.cases.length!==expectedCount || report.coverage?.parameter_combinations!==expectedCount || report.coverage?.execution_combinations!==expectedCount*outcomes.length)throw Error('Matrix manifest count mismatch');
 const ids=new Set(),parameters=new Set(),runs=new Set();
 const validateChecks=items=>{if(!Array.isArray(items)||!items.length||items.some(c=>!['passed','failed'].includes(c.status)))throw Error('Missing or unknown required checks');for(const c of items)if(c.status==='passed' && Object.hasOwn(c,'expected') && Object.hasOwn(c,'actual') && !isDeepStrictEqual(c.expected,c.actual))throw Error('Passed source check contradicts expected/actual evidence');};
 const requiredChecks=[];
 for(const c of report.cases){
  if(typeof c.id!=='string'||!c.id||ids.has(c.id))throw Error('Duplicate or missing case id');ids.add(c.id);
  if(!c.parameters||Object.keys(c.parameters).length!==Object.keys(dimensions).length||Object.entries(dimensions).some(([k,v])=>!v.some(x=>isDeepStrictEqual(x,c.parameters[k]))))throw Error('Case outside manifest dimensions');
  const key=JSON.stringify(Object.keys(dimensions).map(k=>c.parameters[k]));if(parameters.has(key))throw Error('Duplicate parameter combination');parameters.add(key);
  if(typeof c.parameters.nonstop!=='boolean'||typeof c.representatives?.prompt!=='string')throw Error('Missing command metadata');
  validateChecks(c.checks);requiredChecks.push(...c.checks);
  if(!Array.isArray(c.executions)||c.executions.length!==outcomes.length||new Set(c.executions.map(e=>e.outcome)).size!==outcomes.length)throw Error('Missing or duplicate executions');
  for(const e of c.executions){
   if(!outcomes.includes(e.outcome)||typeof e.run_id!=='string'||!e.run_id||runs.has(e.run_id))throw Error('Invalid or duplicate execution id');runs.add(e.run_id);
   if(!Array.isArray(e.events)||!e.canonical_metrics||typeof e.canonical_metrics!=='object')throw Error('Missing canonical execution evidence');
   validateChecks(e.checks);requiredChecks.push(...e.checks);
  }
 }
 const checks=[];
 checks.push({id:'source_matrix_checks',status:requiredChecks.some(c=>c.status==='failed')?'failed':'passed'});
 for(const c of report.cases || [])for(const e of c.executions || []){
  const actual=dashboard.summarizeRoutingAttempts(e.events.map(JSON.stringify),e.run_id);
  checks.push({id:c.id+':'+e.outcome+':full_metrics',status:e.canonical_metrics?isDeepStrictEqual(actual,e.canonical_metrics)?'passed':'failed':'unknown',...(e.canonical_metrics && !isDeepStrictEqual(actual,e.canonical_metrics)?{expected:e.canonical_metrics,actual}:{})});
  const command=buildCommand(c.representatives?.prompt || 'controlled request',c.parameters.nonstop);
  checks.push({id:c.id+':'+e.outcome+':requested_mode_command',status:command.includes('--prompt "/autopilot on ')===c.parameters.nonstop?'passed':'failed'});
 }
 checks.push({id:'runtime_continuation',status:'unknown',reason:'Controlled provider matrix and command prefix do not prove runtime continuation.'});
 return {schema_version:1,source:'controlled_matrix_production_js_python_metrics_parity',scope:'Prompt cases cover routing classification only; expected artifacts and complete task execution are not verified by this matrix.',checks,summary:Object.fromEntries(['passed','failed','unknown'].map(s=>[s,checks.filter(c=>c.status===s).length]))};
}
module.exports={audit,render,auditMatrix,stageChecks,historyChecks};
if(require.main===module){const args=process.argv.slice(2),get=k=>args[args.indexOf(k)+1];if(!args.includes('--root')&&!args.includes('--matrix'))throw Error('Usage: node testcase/evidence_audit.js --root DIR [--expected JSON] | --matrix REPORT');Promise.resolve(args.includes('--matrix')?auditMatrix(JSON.parse(fs.readFileSync(get('--matrix'),'utf8'))):audit(get('--root'),args.includes('--expected')?JSON.parse(fs.readFileSync(get('--expected'),'utf8')):{})).then(result=>{console.log(JSON.stringify(result,null,2));process.exitCode=result.summary.failed?1:0;}).catch(e=>{console.error(e.stack);process.exitCode=1;});}
