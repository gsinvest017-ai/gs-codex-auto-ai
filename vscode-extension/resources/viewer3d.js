import * as THREE from 'three';
import {GLTFLoader} from './three/examples/jsm/loaders/GLTFLoader.js';
import {OrbitControls} from './three/examples/jsm/controls/OrbitControls.js';
const api=acquireVsCodeApi();
const viewport=document.getElementById('viewport'),status=document.getElementById('status');
const scene=new THREE.Scene();scene.background=new THREE.Color('#111720');
const camera=new THREE.PerspectiveCamera(45,1,0.01,10000);camera.position.set(3,2,4);
let renderer;
try {renderer=new THREE.WebGLRenderer({antialias:true});} catch(error) {
 status.textContent='無法啟動 3D 預覽：此視窗的 WebGL 不可用。請檢查 VS Code 硬體加速或顯示驅動。'+error.message;
 api.postMessage({type:'loadError',message:status.textContent});throw error;
}renderer.setPixelRatio(Math.min(devicePixelRatio,2));viewport.appendChild(renderer.domElement);
const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;
scene.add(new THREE.HemisphereLight(0xc8dcff,0x34303b,2.3));const sun=new THREE.DirectionalLight(0xffffff,3);sun.position.set(5,8,6);scene.add(sun);
let model=null,generation=0,currentName=null;
const manager=new THREE.LoadingManager();
manager.setURLModifier(url=>{if(!url.startsWith('data:') && !url.startsWith('blob:'))throw new Error('禁止載入遠端模型資源');return url;});
const loader=new GLTFLoader(manager);
function fit(){if(!model)return;const box=new THREE.Box3().setFromObject(model),center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3());const radius=Math.max(size.length()/2,0.1);const vertical=THREE.MathUtils.degToRad(camera.fov/2),horizontal=Math.atan(Math.tan(vertical)*camera.aspect);const distance=radius/Math.sin(Math.min(vertical,horizontal))*1.25;camera.position.copy(center).add(new THREE.Vector3(1,0.6,1).normalize().multiplyScalar(distance));camera.near=Math.max(radius/1000,0.001);camera.far=radius*1000;camera.updateProjectionMatrix();controls.target.copy(center);controls.update();}
function release(object){object.traverse(node=>{node.geometry?.dispose();for(const mat of (Array.isArray(node.material)?node.material:[node.material]))if(mat){for(const value of Object.values(mat))if(value?.isTexture){value.source?.data?.close?.();value.dispose();}mat.dispose();}});}
function resize(){const w=viewport.clientWidth,h=viewport.clientHeight;camera.aspect=w/Math.max(h,1);camera.updateProjectionMatrix();renderer.setSize(w,h,false);}
new ResizeObserver(resize).observe(viewport);resize();
function render(){requestAnimationFrame(render);controls.update();renderer.render(scene,camera);}render();
document.getElementById('fit').onclick=fit;document.getElementById('reload').onclick=()=>api.postMessage({type:'reload'});
window.addEventListener('message',async({data})=>{
 if(data.type==='error'){status.textContent='預覽失敗：'+data.message;return;}
 if(data.type!=='model')return;const version=++generation;status.textContent='載入模型…';
 try{const loaded=await loader.parseAsync(JSON.stringify(data.json),'');if(version!==generation){release(loaded.scene);return;}
 const sameModel=currentName===data.name;if(model){scene.remove(model);release(model);}model=loaded.scene;scene.add(model);currentName=data.name;
 if(!sameModel)fit();document.getElementById('name').textContent=data.name;status.textContent='模型已載入 · '+(sameModel?'已保留視角':'可旋轉、縮放與平移');api.postMessage({type:'loaded',name:data.name});
 }catch(error){status.textContent='模型載入失敗：'+error.message;api.postMessage({type:'loadError',message:error.message});}
});
api.postMessage({type:'ready'});
