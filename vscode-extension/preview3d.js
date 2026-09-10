// Local-only model viewer. Never executes scripts from the user's project.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const MODEL = /\.(glb|gltf)$/i;
const MAX_BYTES = 100 * 1024 * 1024;
const IGNORED = new Set(['node_modules','.git','.venv','venv','.codex','.claude','vendor','log','tests','test','__pycache__']);
function autoEligible(root,file){const rel=path.relative(root,path.resolve(root,file));return rel!== '..' && !rel.startsWith('..'+path.sep) && !path.isAbsolute(rel) && !rel.split(path.sep).some(part=>IGNORED.has(part.toLowerCase()));}
function inside(root, candidate) {
  const realRoot = fs.realpathSync(root), real = fs.realpathSync(candidate);
  const rel = path.relative(realRoot, real);
  if (rel === '..' || rel.startsWith('..'+path.sep) || path.isAbsolute(rel)) throw new Error('模型與依賴必須位於本專案內');
  return real;
}
function discover(root) {
  const found = new Map(); const queue=[root]; let count=0;
  const add=(file)=>{try {const real=inside(root,file);if(!MODEL.test(real))return;const st=fs.statSync(real);if(st.isFile())found.set(real,{path:path.relative(root,real),format:path.extname(real).slice(1),size:st.size,mtime:st.mtimeMs});}catch{}};
  try {for(const line of fs.readFileSync(path.join(root,'log','workbench-artifacts.jsonl'),'utf8').split(/\r?\n/)){try{const e=JSON.parse(line);add(path.resolve(root,e.relative_path || e.path));}catch{}}}catch{}
  while(queue.length && count<12000){const dir=queue.shift();let entries;try{entries=fs.readdirSync(dir,{withFileTypes:true});}catch{continue;}
    for(const e of entries){count++;if(e.isSymbolicLink())continue;const full=path.join(dir,e.name);if(e.isDirectory() && !IGNORED.has(e.name.toLowerCase()))queue.push(full);else if(e.isFile() && MODEL.test(e.name))add(full);}}
  return [...found.values()].sort((a,b)=>b.mtime-a.mtime || a.path.localeCompare(b.path));
}
function readModel(root, relativePath) {
  const file=inside(root,path.resolve(root,relativePath));if(!MODEL.test(file))throw new Error('僅支援 GLB / glTF 模型');
  const stat=fs.statSync(file);if(stat.size>MAX_BYTES)throw new Error('模型超過 100 MB 預覽限制');
  const bytes=fs.readFileSync(file);let json,bin;
  if(/\.glb$/i.test(file)) {
    if(bytes.length<20 || bytes.readUInt32LE(0)!==0x46546c67 || bytes.readUInt32LE(4)!==2 || bytes.readUInt32LE(8)!==bytes.length)throw new Error('GLB 尚未寫入完成或格式無效');
    let offset=12;while(offset+8<=bytes.length){const size=bytes.readUInt32LE(offset),kind=bytes.readUInt32LE(offset+4);offset+=8;if(offset+size>bytes.length)throw new Error('GLB chunk 不完整');const chunk=bytes.subarray(offset,offset+size);if(kind===0x4e4f534a)json=JSON.parse(chunk.toString('utf8'));if(kind===0x004e4942)bin=chunk;offset+=size;}
  } else json=JSON.parse(bytes.toString('utf8'));
  if(!json || json.asset?.version!=='2.0')throw new Error('需要 glTF 2.0 模型');
  let total=bytes.length;
  const data=(uri)=>{
    if(typeof uri!=='string')throw new Error('無效 glTF URI');
    if(/^data:(application\/(octet-stream|gltf-buffer)|image\/(png|jpeg|webp));base64,[a-z0-9+/=\s]*$/i.test(uri))return uri;
    if(/^[a-z][a-z0-9+.-]*:|^[/\\]|[?#]/i.test(uri))throw new Error('預覽禁止遠端或絕對路徑 glTF 資源');
    const decoded=decodeURIComponent(uri);if(/^[a-z][a-z0-9+.-]*:|^[/\\]/i.test(decoded))throw new Error('無效 glTF 資源路徑');
    const dep=inside(root,path.resolve(path.dirname(file),decoded));const ext=path.extname(dep).toLowerCase();
    const mime={'.bin':'application/octet-stream','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}[ext];if(!mime)throw new Error('不支援的 glTF 依賴格式');
    const st=fs.statSync(dep);total+=st.size;if(total>MAX_BYTES)throw new Error('模型及依賴超過 100 MB');
    return 'data:'+mime+';base64,'+fs.readFileSync(dep).toString('base64');
  };
  for(const [i,buffer] of (json.buffers || []).entries()){if(buffer.uri)buffer.uri=data(buffer.uri);else if(i===0 && bin)buffer.uri='data:application/octet-stream;base64,'+bin.toString('base64');else throw new Error('模型 buffer 缺失');}
  for(const image of json.images || [])if(image.uri)image.uri=data(image.uri);
  // Loader plugins must not introduce unknown external resource access.
  const check=(value)=>{if(!value || typeof value!=='object')return;for(const [key,v] of Object.entries(value)){if(key==='uri' && typeof v==='string' && !v.startsWith('data:'))throw new Error('模型含不允許的外部 URI');if(v && typeof v==='object')check(v);}};check(json);
  return {type:'model',name:path.relative(root,file),json,signature:`${stat.size}:${stat.mtimeMs}`};
}
function html(webview, extensionPath, uriFile = (file) => require('vscode').Uri.file(file)) {
  const resource=(p)=>webview.asWebviewUri(uriFile(path.join(extensionPath,'resources',p))).toString();
  const nonce=crypto.randomBytes(18).toString('base64'); const source=webview.cspSource;
  return `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}' ${source}; style-src 'unsafe-inline'; img-src data: blob:; connect-src data: blob:; worker-src 'none';"><style>body{margin:0;background:#111720;color:#e8eef7;font:13px system-ui}header{padding:10px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;background:#1b2532}button{color:#e8eef7;background:#344358;border:1px solid #66788d;border-radius:5px;padding:7px 12px;cursor:pointer}#viewport{height:calc(100vh - 88px);min-height:200px}canvas{display:block;width:100%;height:100%}#status{padding:9px;color:#bbccdf;white-space:pre-wrap}#name{flex:1;overflow-wrap:anywhere}</style><script type="importmap" nonce="${nonce}">${JSON.stringify({imports:{three:resource('three/build/three.module.js')}})}</script></head><body><header><b id="name">3D 模型預覽</b><button id="fit">適合視窗</button><button id="reload">重新載入</button><span>拖曳旋轉 · 滾輪縮放 · 右鍵平移</span></header><div id="viewport"></div><div id="status" role="status">等待模型…</div><script nonce="${nonce}" type="module" src="${resource('viewer3d.js')}"></script></body></html>`;
}
function openImage(vscode,root,file){const target=inside(root,path.resolve(root,file));if(!/\.(gif|png|jpg|jpeg|webp)$/i.test(target))throw Error('不支援的圖片格式');return vscode.commands.executeCommand('vscode.open',vscode.Uri.file(target),{viewColumn:vscode.ViewColumn.Beside,preserveFocus:true});}
function createController(vscode,extensionPath,{onArtifact=()=>{}}={}) {
  const panels=new Map(),opened=new Set(),pending=new Map(),watchers=new Map();
  const keyOf=(root,file)=>inside(root,path.resolve(root,file));
  function open(root,file,{automatic=false}={}) {
    const key=keyOf(root,file);let entry=panels.get(key);
    if(entry){entry.load();if(!automatic)entry.panel.reveal(vscode.ViewColumn.Beside,true);return entry.panel;}
    if(automatic && opened.has(key))return null;
    // Validation occurs before displaying a panel or accepting external references.
    const first=readModel(root,file);
    const panel=vscode.window.createWebviewPanel('codexautoai3d',`3D · ${path.basename(file)}`,{viewColumn:vscode.ViewColumn.Beside,preserveFocus:true},{enableScripts:true,retainContextWhenHidden:true,localResourceRoots:[vscode.Uri.file(path.join(extensionPath,'resources'))]});
    entry={panel,root,file,ready:false,signature:null,load(){try{const model=readModel(root,file);if(entry.ready){panel.webview.postMessage(model);if(entry.signature!==model.signature)onArtifact({root,path:file});entry.signature=model.signature;}}catch(e){if(entry.ready)panel.webview.postMessage({type:'error',message:e.message});}}};
    panels.set(key,entry);opened.add(key);panel.webview.html=html(panel.webview,extensionPath,vscode.Uri.file);
    panel.webview.onDidReceiveMessage(m=>{if(m.type==='ready'){entry.ready=true;entry.load();}else if(m.type==='reload')entry.load();});
    panel.onDidDispose(()=>panels.delete(key));return panel;
  }
  function changed(root,file) {
    if(!autoEligible(root,file))return;
    const trigger=path.resolve(root,file);if(pending.has(trigger))clearTimeout(pending.get(trigger));let previous=null,tries=0;
    const settle=()=>{let signature;try{const st=fs.statSync(trigger);signature=st.size+':'+st.mtimeMs;}catch{pending.delete(trigger);return;}
      if(previous!==signature && tries++<12){previous=signature;pending.set(trigger,setTimeout(settle,600));return;}
      pending.delete(trigger);try{if(MODEL.test(trigger))open(root,path.relative(root,trigger),{automatic:true});else if(/\.gif$/i.test(trigger)){if(![...opened].some(key=>autoEligible(root,key))){opened.add(keyOf(root,trigger));Promise.resolve(openImage(vscode,root,trigger)).catch(()=>opened.delete(keyOf(root,trigger)));}}else for(const entry of panels.values())if(entry.root===root)entry.load();}catch{if(tries++<12)pending.set(trigger,setTimeout(settle,600));}};
    pending.set(trigger,setTimeout(settle,600));
  }
  function watch(root) {
    if(watchers.has(root))return watchers.get(root);
    const watcher=vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(root,'**/*.{glb,gltf,bin,png,jpg,jpeg,webp,gif}'));
    watcher.onDidCreate(uri=>changed(root,uri.fsPath));watcher.onDidChange(uri=>changed(root,uri.fsPath));watchers.set(root,watcher);return watcher;
  }
  function unwatch(root){watchers.get(root)?.dispose();watchers.delete(root);for(const [file,timer] of pending)if(autoEligible(root,file)){clearTimeout(timer);pending.delete(file);}}
  function openExisting(root){const hits=discover(root).filter(hit=>autoEligible(root,hit.path));if(hits.length)return open(root,hits[0].path,{automatic:true});return null;}
  return {open,watch,unwatch,openExisting,changed,dispose(){for(const t of pending.values())clearTimeout(t);for(const w of watchers.values())w.dispose();for(const p of panels.values())p.panel.dispose();pending.clear();panels.clear();}};
}
module.exports={openImage,inside,autoEligible,discover,readModel,html,createController};
