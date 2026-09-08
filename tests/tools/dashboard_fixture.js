// Local UI QA harness: real dashboard + real Python router, isolated temporary config.
// node tests/tools/dashboard_fixture.js [port] [events.jsonl]
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const dashboard = require('../../vscode-extension/dashboard');
const routing = require('../../vscode-extension/routing');
const root = fs.mkdtempSync(path.join(os.tmpdir(),'codex-ui-qa-'));
const port = Number(process.argv[2] || 8765);
const eventsPath = process.argv[3];
let events = eventsPath ? fs.readFileSync(eventsPath,'utf8').split(/\r?\n/) : [];
const state = () => ({type:'state',exists:false,summary:{},routingStats:dashboard.summarizeRoutingAttempts(events)});
const shim = `<script>
function acquireVsCodeApi(){return {postMessage:async function(message){const response=await fetch('/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(message)});for(const data of await response.json())window.dispatchEvent(new MessageEvent('message',{data}));}};}
window.addEventListener('load',()=>{const label=document.createElement('div');label.textContent='本機 UI 驗證環境：接線設定寫入隔離暫存專案；啟動任務停用。';label.style='padding:10px;background:#544315;color:white';document.body.prepend(label);window.dispatchEvent(new MessageEvent('message',{data:${JSON.stringify(state())}}));});
</script>`;
const page = dashboard.html('Review this implementation').replace("default-src 'none';", "default-src 'none'; connect-src 'self';").replace('<script>',shim+'<script>');
fs.mkdirSync(path.join(__dirname,'../../dist'),{recursive:true});
fs.writeFileSync(path.join(__dirname,'../../dist/dashboard-fixture.html'),page);
http.createServer(async(req,res)=>{
  if(req.method==='GET' && req.url==='/'){res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(page);return;}
  if(req.method!=='POST' || req.url!=='/message'){res.writeHead(404);res.end();return;}
  let body='';for await(const chunk of req){body+=chunk;if(body.length>20000){res.writeHead(413);res.end();return;}}
  let messages=[];
  try {
    const m=JSON.parse(body);
    if(m.type==='catalog') messages=[{type:'catalog',catalog:await routing.getCatalog(root)}];
    else if(m.type==='saveRoute') {await routing.saveRoute(root,m.selection);messages=[{type:'catalog',catalog:await routing.getCatalog(root)},{type:'status',text:'隔離專案場景設定已儲存至 '+root}];}
    else if(m.type==='preset') {await routing.applyPreset(root,m.preset);messages=[{type:'catalog',catalog:await routing.getCatalog(root)}];}
    else if(m.type==='route') messages=[{type:'routePreview',route:await routing.previewRoute(root,m.requirement)}];
    else messages=[{type:'status',text:'UI 驗證環境不啟動真實模型任務。'}];
  } catch(e){messages=[{type:'status',text:e.message}];}
  res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(messages));
}).listen(port,'127.0.0.1',()=>process.stdout.write(JSON.stringify({url:'http://127.0.0.1:'+port,root,eventsPath:eventsPath || null})+'\n'));
