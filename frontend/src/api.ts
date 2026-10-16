const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const tokenKey='meeting-intelligence-dev-token';
export function setToken(token:string){sessionStorage.setItem(tokenKey,token)}
export function getToken(){return sessionStorage.getItem(tokenKey)||''}
export async function request(path:string,method='GET',body?:unknown){
 const r=await fetch(`${API}${path}`,{method,headers:{'Authorization':`Bearer ${getToken()}`,...(body!==undefined?{'Content-Type':'application/json'}:{})},body:body!==undefined?JSON.stringify(body):undefined});
 let data:any;try{data=await r.json()}catch{data={detail:`HTTP ${r.status}`}}
 if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:`Request failed (${r.status})`);return data;
}
export async function sendChunk(mid:string,seq:number,start:number,end:number,blob:Blob){
 const bytes=new Uint8Array(await blob.arrayBuffer());const hash=await crypto.subtle.digest('SHA-256',bytes);const sha=Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,'0')).join('');
 const form=new FormData();form.append('sequence',String(seq));form.append('start_ms',String(start));form.append('end_ms',String(end));form.append('sha256',sha);form.append('audio',blob,`chunk-${seq}.webm`);
 const r=await fetch(`${API}/api/meetings/${mid}/audio-chunks`,{method:'POST',headers:{'Authorization':`Bearer ${getToken()}`},body:form});if(!r.ok)throw new Error(`Audio sync failed: ${r.status} ${await r.text()}`);return r.json();
}
