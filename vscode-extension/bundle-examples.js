// Generate packaged placeholders from the repository's single testcase source.
const fs=require('fs'),path=require('path');
function bundle(source=path.join(__dirname,'../testcase'),target=path.join(__dirname,'resources/testcase')) {
 const manifest=JSON.parse(fs.readFileSync(path.join(source,'cases.json'),'utf8'));
 fs.mkdirSync(target,{recursive:true});
 for(const item of manifest.cases){const relative=item.prompt_file;if(!/^prompts\/[a-z0-9-]+\.md$/.test(relative))throw Error('Invalid prompt path');const out=path.join(target,relative);fs.mkdirSync(path.dirname(out),{recursive:true});fs.copyFileSync(path.join(source,relative),out);}
 fs.copyFileSync(path.join(source,'cases.json'),path.join(target,'cases.json'));
 return manifest.cases.length;
}
if(require.main===module)console.log('Bundled testcase prompts: '+bundle());
module.exports={bundle};
