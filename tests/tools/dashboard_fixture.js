// Real dashboard bridge QA. A specified workspace is read-only; model launch is disabled.
// node tests/tools/dashboard_fixture.js [port] [read-only-workspace-root]
const http=require('http'),fs=require('fs'),os=require('os'),path=require('path');
const {execFile}=require('child_process');
const dashboard=require('../../vscode-extension/dashboard'),routing=require('../../vscode-extension/routing');
const readOnly=!!process.argv[3],root=process.argv[3] || fs.mkdtempSync(path.join(os.tmpdir(),'codex-ui-qa-'));
const port=Number(process.argv[2] || 8765);
const workbench=(action)=>new Promise((resolve,reject)=>execFile('python',[path.join(__dirname,'../../tools/workbench.py'),'--root',root,'--action',action,'--limit','30'],{encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'},windowsHide:true,timeout:15000,maxBuffer:4*1024*1024},(error,stdout)=>{if(error)return reject(error);try{resolve(JSON.parse(stdout));}catch(e){reject(e);}}));
let currentState,currentActivity,receiveMessage;const waiters=[];
const bridge={html:'',postMessage(message){if(message.type==='state')currentState=message;if(message.type==='activity')currentActivity=message;for(const w of [...waiters])if(message.type===w.type || message.type==='status'){waiters.splice(waiters.indexOf(w),1);clearTimeout(w.timer);w.resolve(message);}},onDidReceiveMessage(fn){receiveMessage=fn;return {dispose(){}};}};
const confined=(relative)=>{const real=fs.realpathSync(path.resolve(root,relative)),base=fs.realpathSync(root),rel=path.relative(base,real);if(rel==='..'||rel.startsWith('..'+path.sep)||path.isAbsolute(rel))throw Error('產物必須位於指定工作區');return real;};
const dispose=dashboard.wireDashboard(bridge,{root,defaultReq:'Review this implementation',onActivity:()=>workbench('activity'),onListArtifacts:()=>workbench('artifacts'),onOpenArtifact(relative){const file=confined(relative);bridge.postMessage({type:'fixturePreview',url:'/artifact?path='+encodeURIComponent(path.relative(root,file))});}});
const send=(message,type)=>new Promise((resolve)=>{const w={type,resolve,timer:setTimeout(()=>{const i=waiters.indexOf(w);if(i>=0)waiters.splice(i,1);resolve({type:'status',text:'本地資料讀取逾時'});},20000)};waiters.push(w);receiveMessage(message);});
const shim=`<script>
function acquireVsCodeApi(){return {postMessage:async function(message){const response=await fetch('/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(message)});for(const data of await response.json()){if(data.type==='fixturePreview')window.open(data.url,'_blank');else window.dispatchEvent(new MessageEvent('message',{data}));}}};}
window.addEventListener('load',()=>{const label=document.createElement('div');label.textContent='本機 UI 驗證：正式 bridge 與中央 API；指定工作區唯讀；模型啟動停用。';label.style='padding:10px;background:#544315;color:white';document.body.prepend(label);const poll=async()=>{for(const endpoint of ['/state','/activity']){const data=await(await fetch(endpoint)).json();if(data)window.dispatchEvent(new MessageEvent('message',{data}));}};poll();setInterval(poll,2000);});
</script>`;
const page=bridge.html.replace("default-src 'none';","default-src 'none'; connect-src 'self';").replace('<script>',shim+'<script>');
const server=http.createServer(async(req,res)=>{
  if(req.method==='GET' && ['/state','/activity'].includes(req.url)){res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify((req.url==='/state'?currentState:currentActivity)||null));return;}
  if(req.method==='GET' && req.url==='/'){res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(page);return;}
  if(req.method==='GET' && req.url.startsWith('/artifact?')){try{const file=confined(new URL(req.url,'http://127.0.0.1').searchParams.get('path'));const ext=path.extname(file).toLowerCase(),mime={'.png':'image/png','.glb':'model/gltf-binary','.gltf':'model/gltf+json','.obj':'text/plain; charset=utf-8'}[ext];if(!mime)throw Error('不支援此預覽格式');res.writeHead(200,{'Content-Type':mime,'Content-Security-Policy':"sandbox; default-src 'none'"});fs.createReadStream(file).pipe(res);}catch(e){res.writeHead(403,{'Content-Type':'text/plain; charset=utf-8'});res.end(e.message);}return;}
  if(req.method!=='POST'||req.url!=='/message'){res.writeHead(404);res.end();return;}
  let body='';for await(const chunk of req){body+=chunk;if(body.length>20000){res.writeHead(413);res.end();return;}}
  let messages=[];
  try{const m=JSON.parse(body);if(readOnly&&['saveRoute','preset'].includes(m.type))throw Error('真實工作區唯讀驗證，不修改設定。');
    if(m.type==='catalog')messages=[{type:'catalog',catalog:await routing.getCatalog(root)}];
    else if(m.type==='saveRoute'){await routing.saveRoute(root,m.selection);messages=[{type:'catalog',catalog:await routing.getCatalog(root)}];}
    else if(m.type==='preset'){await routing.applyPreset(root,m.preset);messages=[{type:'catalog',catalog:await routing.getCatalog(root)}];}
    else if(m.type==='route')messages=[{type:'routePreview',route:await routing.previewRoute(root,m.requirement)}];
    else if(m.type==='history')messages=[await send(m,'history')];
    else if(m.type==='artifacts')messages=[await send(m,'artifacts')];
    else if(m.type==='openArtifact')messages=[await send(m,'fixturePreview')];
    else messages=[{type:'status',text:'UI 驗證環境不啟動真實模型任務。'}];
  }catch(e){messages=[{type:'status',text:e.message}];}
  res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(messages));
});server.on('close',dispose);server.listen(port,'127.0.0.1',()=>process.stdout.write(JSON.stringify({url:'http://127.0.0.1:'+port,root,readOnly})+'\n'));
