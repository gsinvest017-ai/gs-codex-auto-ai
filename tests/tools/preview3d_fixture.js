// node tests/tools/preview3d_fixture.js [port] [workspace] [relative GLB]
const fs=require('fs'),path=require('path'),http=require('http');
const {html,readModel,inside}=require('../../vscode-extension/preview3d');
const port=Number(process.argv[2]||8767),root=process.argv[3]||'C:/Users/User/Desktop/codexautoai-sandbox',file=process.argv[4]||'assets/codexautoai-avatar.glb';
const ext=path.resolve(__dirname,'../../vscode-extension'),resources=path.join(ext,'resources');
const view={cspSource:"'self'",asWebviewUri:p=>'/resources/'+path.relative(resources,p).replaceAll('\\','/')};
let page=html(view,ext,p=>p);const nonce=page.match(/nonce="([^"]+)"/)[1];
page=page.replace('connect-src data: blob:','connect-src data: blob: \'self\'');
page=page.replace('<body>',`<body><script nonce="${nonce}">function acquireVsCodeApi(){return {postMessage:async(m)=>{if(m.type==='ready'||m.type==='reload'){const response=await fetch('/model');const data=await response.json();window.dispatchEvent(new MessageEvent('message',{data}));}}};}</script>`);
http.createServer((req,res)=>{try{if(req.url==='/'){res.writeHead(200,{'Content-Type':'text/html'});return res.end(page);}if(req.url==='/model'){res.writeHead(200,{'Content-Type':'application/json'});return res.end(JSON.stringify(readModel(root,file)));}if(req.url.startsWith('/resources/')){const f=inside(resources,path.resolve(resources,decodeURIComponent(req.url.slice(11))));res.writeHead(200,{'Content-Type':'text/javascript'});return res.end(fs.readFileSync(f));}res.writeHead(404);res.end();}catch(e){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({type:'error',message:e.message}));}}).listen(port,'127.0.0.1',()=>console.log('http://127.0.0.1:'+port));
