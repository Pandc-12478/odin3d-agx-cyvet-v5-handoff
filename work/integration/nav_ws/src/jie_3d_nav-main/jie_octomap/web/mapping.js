import * as THREE from "./vendor/three.module.js";
const canvas = document.getElementById("view");
const renderer = new THREE.WebGLRenderer({canvas, antialias:false});
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x111923);
const camera = new THREE.PerspectiveCamera(60, 1, .05, 300);
camera.position.set(9, 8, 9);
const controls = {yaw:.8, pitch:.5, distance:15, target:new THREE.Vector3()};
const grid = new THREE.GridHelper(20, 20, 0x4c6a75, 0x334451);
scene.add(grid);
const dots = new Map();
const material = new THREE.PointsMaterial({color:0x79dbd4,size:.055});
let cloud, trackLine, robot;
const robotGeometry = new THREE.SphereGeometry(.17,8,6);
robot = new THREE.Mesh(robotGeometry,new THREE.MeshBasicMaterial({color:0xffb566}));
scene.add(robot);
let sequence=0, busy=false;
const api = `http://${location.hostname}:8082`;
const actionStatus = document.getElementById("action-status");
function refreshGeometry(){
  if(cloud){scene.remove(cloud);cloud.geometry.dispose()}
  const values=[...dots.values()].flatMap(([x,y,z])=>[x,z,y]);
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute("position",new THREE.Float32BufferAttribute(values,3));
  cloud=new THREE.Points(geometry,material);scene.add(cloud);
}
function refreshTrack(points){
  if(trackLine){scene.remove(trackLine);trackLine.geometry.dispose()}
  const geometry=new THREE.BufferGeometry().setFromPoints(points.map(p=>new THREE.Vector3(p[0],p[2],p[1])));
  trackLine=new THREE.Line(geometry,new THREE.LineBasicMaterial({color:0xffa964}));scene.add(trackLine);
}
async function poll(){
  if(busy)return;busy=true;
  try{
    const res=await fetch(`${api}/api/status?since=${sequence}`,{cache:"no-store"});
    if(!res.ok)throw Error(`HTTP ${res.status}`);
    const data=await res.json();
    if(data.snapshot){dots.clear();for(const [key,point] of data.points)dots.set(key,point)}
    else for(const [op,key,point] of data.changes){if(op==="set")dots.set(key,point);else dots.delete(key)}
    sequence=data.sequence;refreshGeometry();
    document.getElementById("status").dataset.saved=String(!!data.saved);
    refreshTrack(data.track||[]);
    if(data.pose)robot.position.set(data.pose[0],data.pose[2],data.pose[1]);
    document.getElementById("status").textContent =
      `SLAM 点云 ${data.cloud_age==null?"无":data.cloud_age+" 秒前"} · ROS2 ${data.cloud_age!=null&&data.cloud_age<3?"在线":"等待"} · ${data.active?"建图中":"未建图"} · ${data.job||"空闲"} · ${dots.size} 点`;
    document.getElementById("files").textContent=(data.bins||[]).map(x=>`${x.name} (${(x.bytes/1048576).toFixed(1)} MiB)`).join("\n")||data.dest||"尚无";
    document.getElementById("log").textContent=(data.result||"")+"\n"+(data.log_tail||[]).join("\n");
    document.querySelectorAll("button[data-action]").forEach(x=>x.disabled=!!data.job);
  }catch(e){document.getElementById("status").textContent=`网络断开：${e.message}`}
  busy=false;
}
document.querySelectorAll("button[data-action]").forEach(button=>button.onclick=async()=>{
  const action=button.dataset.action;
  let requested=action;
  if(action==="stop"){
    const saved=document.getElementById("status").dataset.saved==="true";
    if(!saved){
      if(!confirm("尚未确认保存成功。停止将放弃本次扫描，但保留已有 BIN 文件。继续？"))return;
      requested="discard";
    }else if(!confirm("确认安全停止建图并恢复配置？"))return;
  }
  const token=document.getElementById("token").value.trim();
  if(!token){actionStatus.textContent="请先输入控制令牌";return}
  actionStatus.textContent="请求已发送，等待响应…";
  try{
    const res=await fetch(`${api}/api/${requested}`,{method:"POST",headers:{"X-Mapping-Token":token}});
    const data=await res.json();
    if(!res.ok){
      actionStatus.textContent=res.status===403
        ? "控制令牌不正确。请核对 AGX 的 ap_mapping/control.token，粘贴时不要包含引号"
        : `操作失败：${data.error||res.status}`;
      return;
    }
    actionStatus.textContent=`已提交${button.textContent}，请查看下方操作与日志`;
  }catch(e){actionStatus.textContent=`请求失败：${e.message}`}
  poll();
});
function resize(){const r=canvas.getBoundingClientRect();renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix()}
const pointers=new Map();let last;
canvas.onpointerdown=e=>{canvas.setPointerCapture(e.pointerId);pointers.set(e.pointerId,[e.clientX,e.clientY]);last=[e.clientX,e.clientY]};
canvas.onpointerup=e=>{pointers.delete(e.pointerId);last=null};
canvas.onpointermove=e=>{
 if(!pointers.has(e.pointerId))return;
 const dx=e.clientX-last[0],dy=e.clientY-last[1];last=[e.clientX,e.clientY];
 if(e.buttons===2||pointers.size>1){controls.target.x-=dx*.02;controls.target.z+=dy*.02}
 else{controls.yaw+=dx*.006;controls.pitch=Math.max(-1.45,Math.min(1.45,controls.pitch+dy*.006))}
};
canvas.oncontextmenu=e=>e.preventDefault();
canvas.onwheel=e=>{e.preventDefault();controls.distance=Math.max(1,Math.min(100,controls.distance*Math.exp(e.deltaY*.001)))};
function animate(){
 resize();
 const c=controls,t=c.target;
 camera.position.set(t.x+c.distance*Math.cos(c.pitch)*Math.sin(c.yaw),t.y+c.distance*Math.sin(c.pitch),t.z+c.distance*Math.cos(c.pitch)*Math.cos(c.yaw));
 camera.lookAt(t);renderer.render(scene,camera);requestAnimationFrame(animate);
}
animate();setInterval(poll,1500);poll();
