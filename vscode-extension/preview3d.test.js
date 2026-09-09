const test=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),os=require('os'),path=require('path');
const model=require('./preview3d');
function workspace(){return fs.mkdtempSync(path.join(os.tmpdir(),'codex-3d-'));}
function glb(json={asset:{version:'2.0'},scenes:[{nodes:[]}],scene:0}){let text=Buffer.from(JSON.stringify(json));text=Buffer.concat([text,Buffer.alloc((4-text.length%4)%4,32)]);const header=Buffer.alloc(20);header.writeUInt32LE(0x46546c67,0);header.writeUInt32LE(2,4);header.writeUInt32LE(20+text.length,8);header.writeUInt32LE(text.length,12);header.writeUInt32LE(0x4e4f534a,16);return Buffer.concat([header,text]);}
test('GLB validates complete structure; local glTF dependencies embed without remote access',()=>{
 const root=workspace();fs.writeFileSync(path.join(root,'a.glb'),glb());assert.equal(model.readModel(root,'a.glb').json.asset.version,'2.0');
 fs.writeFileSync(path.join(root,'a.glb'),glb().subarray(0,22));assert.throws(()=>model.readModel(root,'a.glb'),/完整|無效/);
 const json={asset:{version:'2.0'},buffers:[{uri:'mesh.bin',byteLength:4}]};fs.writeFileSync(path.join(root,'mesh.bin'),Buffer.from([1,2,3,4]));
 fs.writeFileSync(path.join(root,'a.gltf'),JSON.stringify(json));assert.match(model.readModel(root,'a.gltf').json.buffers[0].uri,/^data:application\/octet-stream;base64,/);
 for(const uri of ['https://example.com/private.bin','file:///secret.bin','//server/share.bin','%68ttps%3A%2F%2Fexample.com/a.bin','../outside.bin','data:text/html;base64,SGk=']){json.buffers[0].uri=uri;fs.writeFileSync(path.join(root,'a.gltf'),JSON.stringify(json));assert.throws(()=>model.readModel(root,'a.gltf'),undefined,uri);}
});
test('discovery excludes dependency directories and out-of-root registry references',()=>{
 const root=workspace();fs.mkdirSync(path.join(root,'assets'));fs.writeFileSync(path.join(root,'assets','ok.glb'),glb());fs.mkdirSync(path.join(root,'node_modules'));fs.writeFileSync(path.join(root,'node_modules','hidden.glb'),glb());fs.mkdirSync(path.join(root,'log'));fs.writeFileSync(path.join(root,'log','workbench-artifacts.jsonl'),JSON.stringify({path:'../../outside.glb'}));assert.deepEqual(model.discover(root).map(x=>x.path),[path.join('assets','ok.glb')]);
});
function mockApi(){let creates=[],messages=[],handler,disposeFn;const panel={webview:{cspSource:'test:',asWebviewUri:p=>({toString:()=>String(p)}),postMessage:m=>messages.push(m),onDidReceiveMessage:fn=>handler=fn},onDidDispose:fn=>disposeFn=fn,reveal(){},dispose(){disposeFn?.();}};return {api:{Uri:{file:p=>p},ViewColumn:{Beside:2},window:{createWebviewPanel(...args){creates.push(args);return panel;}}},creates,messages,panel,ready:()=>handler({type:'ready'})};}
test('model panel opens beside preserving focus, waits until ready, reloads existing panel, never auto-reopens closed model',()=>{
 const root=workspace(),file=path.join(root,'a.glb');fs.writeFileSync(file,glb());const mock=mockApi();let registered=0;const c=model.createController(mock.api,__dirname,{onArtifact:()=>registered++});
 c.open(root,'a.glb',{automatic:true});assert.equal(mock.creates.length,1);assert.deepEqual(mock.creates[0][2],{viewColumn:2,preserveFocus:true});assert.equal(mock.messages.length,0);mock.ready();assert.equal(mock.messages[0].type,'model');assert.equal(registered,1);
 const previousHtml=mock.panel.webview.html;c.open(root,'a.glb',{automatic:true});assert.equal(mock.creates.length,1);assert.equal(mock.panel.webview.html,previousHtml);assert.equal(registered,1);mock.panel.dispose();assert.equal(c.open(root,'a.glb',{automatic:true}),null);c.dispose();
});
test('file change burst waits for stable size before a single automatic open',()=>{
 const root=workspace(),file=path.join(root,'a.glb');fs.writeFileSync(file,glb());const mock=mockApi(),c=model.createController(mock.api,__dirname);const originalSet=global.setTimeout,originalClear=global.clearTimeout;let seq=0;const timers=new Map();
 global.setTimeout=fn=>{timers.set(++seq,fn);return seq;};global.clearTimeout=id=>timers.delete(id);
 try{c.changed(root,'a.glb');c.changed(root,'a.glb');assert.equal(timers.size,1);let fn=[...timers.values()][0];timers.clear();fn();assert.equal(mock.creates.length,0);fn=[...timers.values()][0];timers.clear();fn();assert.equal(mock.creates.length,1);}finally{c.dispose();global.setTimeout=originalSet;global.clearTimeout=originalClear;}
});
test('MCP provider is optional and uses stable local positional definition API',()=>{
 const Module=require('module'),original=Module._load;Module._load=function(name,...args){if(name==='vscode')return {};return original.call(this,name,...args);};let extension;try{extension=require('./extension');}finally{Module._load=original;}
 assert.equal(extension.registerWorkbenchMcp({}, {subscriptions:[]}, '/workspace'),false);
 let provider;const api={Uri:{file:p=>p},lm:{registerMcpServerDefinitionProvider(id,p){provider=p;return {dispose(){}};}},McpStdioServerDefinition:class{constructor(label,command,args,env,version){Object.assign(this,{label,command,args,env,version});}}};const context={extensionPath:__dirname,subscriptions:[]};
 assert.equal(extension.registerWorkbenchMcp(api,context,'C:/workspace'),true);const def=provider.provideMcpServerDefinitions()[0];assert.equal(def.label,'CodexAutoAI Workbench');assert.equal(def.command,'python');assert.deepEqual(def.args.slice(-2),['--root','C:/workspace']);assert.equal(def.cwd,'C:/workspace');assert.equal(context.subscriptions.length,1);
});


test('automatic model preview ignores test, log and dependency artifacts',()=>{
 const root=workspace();for(const file of ['log/export-integration/a.glb','node_modules/a.glb','.venv/a.glb','tests/a.glb','../outside.glb'])assert.equal(model.autoEligible(root,file),false,file);
 assert.equal(model.autoEligible(root,'assets/avatar.glb'),true);
 const mock=mockApi(),c=model.createController(mock.api,__dirname);for(const file of ['log/a.glb','tests/a.glb'])c.changed(root,file);assert.equal(mock.creates.length,0);c.dispose();
});

test('stable changed revision sends new model content without creating another panel',()=>{
 const root=workspace(),file=path.join(root,'a.glb');fs.writeFileSync(file,glb({asset:{version:'2.0'},scenes:[{nodes:[0]}],nodes:[{name:'before'}]}));
 const mock=mockApi(),c=model.createController(mock.api,__dirname);c.open(root,'a.glb',{automatic:true});mock.ready();assert.equal(mock.messages.at(-1).json.nodes[0].name,'before');
 const originalSet=global.setTimeout,originalClear=global.clearTimeout;let seq=0;const timers=new Map();global.setTimeout=fn=>{timers.set(++seq,fn);return seq;};global.clearTimeout=id=>timers.delete(id);
 try{
   fs.writeFileSync(file,glb({asset:{version:'2.0'},scenes:[{nodes:[0]}],nodes:[{name:'after-new-revision'}]}));c.changed(root,'a.glb');
   for(let tick=0;tick<2;tick++){const fn=[...timers.values()][0];timers.clear();fn();}
   assert.equal(mock.creates.length,1);assert.equal(mock.messages.at(-1).json.nodes[0].name,'after-new-revision');
 }finally{c.dispose();global.setTimeout=originalSet;global.clearTimeout=originalClear;}
});
