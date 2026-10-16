export interface PendingChunk { id:string; meetingId:string; sequence:number; startMs:number; endMs:number; blob:Blob }
const dbName='meeting-intelligence-recovery';const table='chunks';
function open():Promise<IDBDatabase>{return new Promise((resolve,reject)=>{const r=indexedDB.open(dbName,1);r.onupgradeneeded=()=>{const d=r.result;if(!d.objectStoreNames.contains(table))d.createObjectStore(table,{keyPath:'id'})};r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)})}
async function txn<T>(mode:IDBTransactionMode,act:(store:IDBObjectStore,resolve:(v:T)=>void,reject:(e:any)=>void)=>void):Promise<T>{const db=await open();return new Promise((resolve,reject)=>{const t=db.transaction(table,mode);act(t.objectStore(table),resolve,reject);t.oncomplete=()=>db.close();t.onerror=()=>{db.close();reject(t.error)}})}
export function saveChunk(c:PendingChunk){return txn<void>('readwrite',(s,resolve,reject)=>{const r=s.put(c);r.onsuccess=()=>resolve();r.onerror=()=>reject(r.error)})}
export function removeChunk(id:string){return txn<void>('readwrite',(s,resolve,reject)=>{const r=s.delete(id);r.onsuccess=()=>resolve();r.onerror=()=>reject(r.error)})}
export function pendingChunks():Promise<PendingChunk[]>{return txn('readonly',(s,resolve,reject)=>{const r=s.getAll();r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)})}
export interface PendingSegment {id:string; meetingId:string; segments:any[]}
const segmentsTable='meeting-intelligence-segments';
export function localSegmentsKey(mid:string){return `${segmentsTable}-${mid}`}
export function savePendingSegment(mid:string,seg:any){const k=localSegmentsKey(mid);const all=JSON.parse(localStorage.getItem(k)||'[]');if(!all.some((x:any)=>x.client_event_id===seg.client_event_id)){all.push(seg);localStorage.setItem(k,JSON.stringify(all))}}
export function loadPendingSegments(mid:string):any[]{return JSON.parse(localStorage.getItem(localSegmentsKey(mid))||'[]')}
export function dropPendingSegment(mid:string,eventId:string){const k=localSegmentsKey(mid);localStorage.setItem(k,JSON.stringify(loadPendingSegments(mid).filter((x:any)=>x.client_event_id!==eventId)))}
