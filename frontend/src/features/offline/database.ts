import type {Envelope,VaultCapability,VaultHeader} from './types.ts'
export type PrivateRow=Envelope&{id:string;owner:string;version:number}
const DATABASE='ai-notes:offline:v1'
export const request=<T,>(r:IDBRequest<T>)=>new Promise<T>((resolve,reject)=>{r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)})
const complete=(tx:IDBTransaction)=>new Promise<void>((resolve,reject)=>{tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error||new Error('本机存储事务失败'));tx.onabort=()=>reject(tx.error||new Error('本机存储事务已取消'))})
export function createDatabase(factory:IDBFactory=globalThis.indexedDB){
  let opened:Promise<IDBDatabase>|undefined
  const db=()=>opened??=(async()=>{
    if(!factory)throw new Error('浏览器不支持 IndexedDB，不能保存离线资料')
    const r=factory.open(DATABASE,1)
    r.onupgradeneeded=()=>{const d=r.result;d.createObjectStore('headers',{keyPath:'owner'});const records=d.createObjectStore('records',{keyPath:'id'});records.createIndex('owner','owner');d.createObjectStore('leases',{keyPath:'owner'})}
    const d=await request(r);d.onversionchange=()=>{d.close();opened=undefined};return d
  })()
  async function headers(){const d=await db(),tx=d.transaction('headers');return request(tx.objectStore('headers').getAll()) as Promise<VaultHeader[]>}
  async function header(owner:string){const d=await db();return request(d.transaction('headers').objectStore('headers').get(owner)) as Promise<VaultHeader|undefined>}
  async function read(cap:VaultCapability,key:string){cap.check();const d=await db();cap.check();const row=await request(d.transaction('records').objectStore('records').get(`${cap.owner}:${key}`)) as PrivateRow|undefined;cap.check();return row}
  async function put(cap:VaultCapability,expected:number,rows:PrivateRow[]){
    cap.check();const d=await db();cap.check();const tx=d.transaction(['records','headers'],'readwrite'),done=complete(tx),store=tx.objectStore('records')
    let mismatch=false
    const abort=()=>{try{tx.abort()}catch{/* Already committed or aborted. */}}
    cap.signal.addEventListener('abort',abort,{once:true})
    const h=tx.objectStore('headers').get(cap.owner)
    h.onsuccess=()=>{if(!h.result){abort();return}const r=store.get(`${cap.owner}:workspace`)
      r.onsuccess=()=>{try{cap.check();if((r.result?.version||0)!==expected){mismatch=true;tx.abort();return}for(const row of rows){if(row.owner!==cap.owner)throw new Error('资料归属不符');store.put(row)}}catch{tx.abort()}}
    }
    try{await done}catch(error){if(mismatch)throw new ConcurrentWriteError();throw error}finally{cap.signal.removeEventListener('abort',abort)}cap.check()
  }
  async function initialize(header:VaultHeader,row:PrivateRow,check:()=>void){
    check();const d=await db();check();const tx=d.transaction(['headers','records'],'readwrite'),done=complete(tx)
    const r=tx.objectStore('headers').get(header.owner)
    let exists=false
    r.onsuccess=()=>{try{check();if(r.result){exists=true;tx.abort();return}tx.objectStore('headers').add(header);tx.objectStore('records').add(row)}catch{tx.abort()}}
    try{await done}catch(error){if(exists)throw new Error('这个账号已有本机离线库，请解锁或明确清除后重建');throw error}
  }
  async function clear(owner:string){
    const d=await db(),tx=d.transaction(['headers','records','leases'],'readwrite'),done=complete(tx)
    tx.objectStore('headers').delete(owner);tx.objectStore('leases').delete(owner)
    const r=tx.objectStore('records').index('owner').openKeyCursor(IDBKeyRange.only(owner))
    r.onsuccess=()=>{const cursor=r.result;if(cursor){tx.objectStore('records').delete(cursor.primaryKey);cursor.continue()}}
    await done
    if(await header(owner))throw new Error('未能确认离线资料已经清除')
  }
  async function lease(owner:string,holder:string,action:'acquire'|'renew'|'release'){
    const d=await db(),tx=d.transaction('leases','readwrite'),done=complete(tx),s=tx.objectStore('leases');let ok=false
    const r=s.get(owner)
    r.onsuccess=()=>{const old=r.result,now=Date.now();if(action==='release'){if(old?.holder===holder)s.delete(owner);ok=true}else if(!old||old.holder===holder||old.expires<now){s.put({owner,holder,expires:now+15000});ok=true}}
    await done;return ok
  }
  return {headers,header,read,put,initialize,clear,lease}
}
export class ConcurrentWriteError extends Error{constructor(){super('另一标签页更新了离线资料，请重试')}}
export const database=createDatabase()
export type OfflineDatabase=ReturnType<typeof createDatabase>
