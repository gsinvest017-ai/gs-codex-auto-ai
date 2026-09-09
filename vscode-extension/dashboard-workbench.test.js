const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('fs'),os=require('os'),path=require('path'),vm=require('vm');
const dashboard=require('./dashboard');
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('history is project-local, bounded, excludes current run and preserves unknown usage',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'project-history-'));fs.mkdirSync(path.join(root,'log'));
  const events=[];for(let i=0;i<25;i++)events.push({type:'model_attempt',run_id:'old-'+i,attempt_id:'old-'+i+':1',actual_provider:'codex',outcome:'ok',usage:{output_tokens:i}});
  events.push({type:'model_attempt',run_id:'current',attempt_id:'current:1',actual_provider:'codex',outcome:'ok',usage:{input_tokens:999999}});
  fs.writeFileSync(path.join(root,'log','events.jsonl'),'x'.repeat(2*1024*1024)+'\n'+events.map(JSON.stringify).join('\n'));
  const original=os.homedir;os.homedir=()=>{throw Error('must not scan account histories');};
  try {const history=await dashboard.loadProjectHistory(root,'current');assert.equal(history.source,'project_events');assert.equal(history.partial,true);assert.equal(history.runs.length,20);assert.equal(history.runs[0].run_id,'old-24');assert.ok(history.runs.every(r=>r.run_id!=='current'));assert.equal(history.runs[0].providers.codex.inTok,null);assert.equal(history.runs[0].providers.codex.outTok,24);}finally{os.homedir=original;}
});

test('formal bridge loads activity asynchronously and history on demand',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'workbench-bridge-'));fs.mkdirSync(path.join(root,'log'));
  fs.writeFileSync(path.join(root,'log','events.jsonl'),JSON.stringify({type:'model_attempt',run_id:'old',attempt_id:'old:1',actual_provider:'codex',outcome:'ok'}));
  fs.writeFileSync(path.join(root,'log','app-run.json'),JSON.stringify({run_id:'now',status:'running',started_at:Date.now()/1000,updated_at:Date.now()/1000}));
  let receive,resolveActivity;const messages=[],opened=[];
  const bridge={html:'',postMessage(m){messages.push(m);},onDidReceiveMessage(fn){receive=fn;return {dispose(){}};}};
  const dispose=dashboard.wireDashboard(bridge,{root,onActivity:()=>new Promise(resolve=>{resolveActivity=resolve;}),onListArtifacts:()=>({items:[{relative_path:'output/model.glb',format:'glb'}]}),onOpenArtifact(p){opened.push(p);}});
  try {
    assert.equal(messages[0].type,'state');assert.equal(messages.some(m=>m.type==='history'),false);
    await tick();resolveActivity({items:[{kind:'command_execution',text:'Build output/model.glb',status:'in_progress'}]});await tick();
    assert.equal(messages.find(m=>m.type==='activity').activity.items[0].text,'Build output/model.glb');
    receive({type:'history'});for(let i=0;i<50&&!messages.some(m=>m.type==='history');i++)await new Promise(r=>setTimeout(r,5));
    assert.equal(messages.find(m=>m.type==='history').history.runs[0].run_id,'old');
    receive({type:'artifacts'});await tick();assert.equal(messages.find(m=>m.type==='artifacts').result.items[0].relative_path,'output/model.glb');
    receive({type:'openArtifact',id:'output/model.glb'});assert.deepEqual(opened,['output/model.glb']);
  }finally{dispose();}
});

test('workbench UI displays central activity, loaded history and preview controls without changing request',()=>{
 const elements=new Map(),sent=[];let listener;
 const elem=()=>({value:'',textContent:'',innerHTML:'',style:{},children:[],appendChild(e){this.children.push(e);},replaceChildren(){this.children=[];}});
 const document={getElementById(id){if(!elements.has(id))elements.set(id,elem());return elements.get(id);},createElement:elem};
 const page=dashboard.html('');vm.runInNewContext(page.match(/<script>([\s\S]*?)<\/script>/)[1],{document,acquireVsCodeApi:()=>({postMessage:m=>sent.push(m)}),window:{addEventListener:(name,fn)=>listener=fn}});
 const $=id=>document.getElementById(id);$('req').value='unfinished request';
 listener({data:{type:'activity',activity:{last_update:1700000000,items:[{text:'Writing output/model.glb',status:'in_progress',provider:'codex'}]}}});
 assert.match($('activityCurrent').textContent,/正在進行/);assert.match($('activityTimeline').children[0].textContent,/model.glb/);
 listener({data:{type:'state',exists:false,summary:{},run:{run_id:'new-task',status:'running'},routingStats:dashboard.summarizeRoutingAttempts([])}});
 listener({data:{type:'activity',activity:{run_id:'old-task',items:[{text:'stale task output',status:'in_progress'}]}}});
 assert.doesNotMatch($('activityCurrent').textContent,/stale/);assert.equal($('activityTimeline').children.length,0);
 $('btnHistory').onclick();assert.equal(sent.at(-1).type,'history');assert.equal($('btnHistory').disabled,true);
 listener({data:{type:'history',history:{notice:'本專案資料',runs:[]}}});assert.equal($('btnHistory').disabled,false);assert.match($('projectHistoryNotice').textContent,/本專案/);
 listener({data:{type:'artifacts',result:{items:[{relative_path:'output/model.glb',format:'glb'}]}}});$('artifactList').children[0].children[0].onclick();assert.equal(sent.at(-1).id,'output/model.glb');assert.equal($('req').value,'unfinished request');
 assert.match(page,/<details><summary>查看模型、Token 與原始呼叫明細<\/summary>/);
 listener({data:{type:'state',exists:true,summary:{historyLoaded:false,marker:0,completed:[],started:[],runStatus:'completed'},run:{run_id:'graph-run',status:'completed',route:{mode:'graph',graph_id:'g'}},routingStats:dashboard.summarizeRoutingAttempts([]),graphResult:{status:'completed',graph_states:{first:'quota_exhausted',backup:'ok',unused:'skipped'}}}});
 assert.match($('progressTitle').textContent,/非七階段交付/);
 assert.match($('phaseText').textContent,/first：額度耗盡.*backup：成功.*unused：未執行/);
 assert.match($('phaseText').textContent,/未驗證七階段交付/);
 assert.equal($('bar').textContent,'');
});


test('history task status uses result evidence, never a successful model call',async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'history-task-proof-'));fs.mkdirSync(path.join(root,'log'));
 const events=[{type:'model_attempt',run_id:'blocked-task',attempt_id:'a',actual_provider:'codex',outcome:'ok'}];
 fs.writeFileSync(path.join(root,'log','events.jsonl'),events.map(JSON.stringify).join('\n'));
 fs.writeFileSync(path.join(root,'log','task-result-blocked-task.json'),JSON.stringify({schema_version:1,run_id:'blocked-task',status:'blocked',started_at:100,ended_at:200}));
 let history=await dashboard.loadProjectHistory(root);assert.equal(history.runs[0].task_status,'blocked');assert.equal(history.runs[0].latest,'最後呼叫：成功');
 fs.writeFileSync(path.join(root,'log','task-result-blocked-task.json'),JSON.stringify({run_id:'wrong-task',status:'completed'}));
 history=await dashboard.loadProjectHistory(root);assert.equal(history.runs[0].task_status,'unknown');
});

test('activity headlines hide escaped commands and strip markdown link targets',()=>{
 assert.equal(dashboard.activityHeadline({kind:'command_execution',text:'powershell '+'-Command "& { literal escaped data }"' }), '執行命令');
 assert.equal(dashboard.readableActivityText('## 已完成\n[模型](file:///private/full/path/model.glb)'), '已完成');
 assert.equal(dashboard.readableActivityText('[模型](https://example.test/a/really/long/path) 已交付。\nMore'), '模型 已交付。');
});

test('history completed requires a scoped delivery envelope without rehashing historical artifacts',async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'history-completion-proof-'));fs.mkdirSync(path.join(root,'log'));
 const run_id='delivered', resultPath=path.join(root,'log','task-result-'+run_id+'.json');
 fs.writeFileSync(path.join(root,'log','events.jsonl'),[
  {event_type:'run_start',run_id,ts:new Date(100000).toISOString()},
  {type:'model_attempt',run_id,attempt_id:'a',actual_provider:'codex',outcome:'ok'}
 ].map(JSON.stringify).join('\n'));
 const proof={schema_version:1,run_id,status:'completed',started_at:100,ended_at:200,completion_evidence:{event_type:'phase_end',run_id,ts:new Date(150000).toISOString(),phase:7,status:'success',artifacts:[{path:'historical-file-no-longer-present.txt',sha256:'a'.repeat(64)}]}};
 async function status(result){fs.writeFileSync(resultPath,JSON.stringify(result));return (await dashboard.loadProjectHistory(root)).runs[0].task_status;}
 assert.equal(await status(proof),'completed');
 assert.equal(await status({run_id,status:'completed'}),'incomplete');
 for(const mutation of [
  {schema_version:0},{started_at:99},{ended_at:99},
  {completion_evidence:{...proof.completion_evidence,run_id:'another-run'}},
  {completion_evidence:{...proof.completion_evidence,ts:new Date(99000).toISOString()}},
  {completion_evidence:{...proof.completion_evidence,phase:6}},
  {completion_evidence:{...proof.completion_evidence,status:'failed'}},
  {completion_evidence:{...proof.completion_evidence,artifacts:[{path:'file.txt',sha256:'invalid'}]}}
 ])assert.equal(await status({...proof,...mutation}),'incomplete',JSON.stringify(mutation));
});
