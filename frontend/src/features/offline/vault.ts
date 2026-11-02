import {createHeader,decode,encode,open,seal,unlockHeader} from './crypto.ts'
import {database} from './database.ts'
import {authSession} from '../../session.ts'
import {emptyWorkspace,type VaultCapability} from './types.ts'
let generation=0,current:VaultCapability|null=null,controller=new AbortController()
let state:{cap:VaultCapability|null;warning:string;failedClearOwner:string|null}={cap:null,warning:'',failedClearOwner:null}
const listeners=new Set<()=>void>(),urls=new Set<string>()
const notify=()=>{state={...state,cap:current};listeners.forEach(fn=>fn())}
const channel=typeof BroadcastChannel==='undefined'?null:new BroadcastChannel('ai-notes:offline-lock:v1')
export function lockVault(){controller.abort();controller=new AbortController();generation++;current=null;for(const url of urls)URL.revokeObjectURL(url);urls.clear();notify()}
export function vaultUrl(cap:VaultCapability,blob:Blob){cap.check();const url=URL.createObjectURL(blob);urls.add(url);return url}
export function revokeVaultUrl(url:string){URL.revokeObjectURL(url);urls.delete(url)}
function activate(owner:string,key:CryptoKey){
  lockVault();const next=generation,signal=controller.signal
  const cap:VaultCapability=Object.freeze({owner,generation:next,key,signal,check(){if(current!==cap||generation!==next||signal.aborted)throw new DOMException('本机离线库已锁定','AbortError')}})
  current=cap;notify();return cap
}
export async function unlockVault(owner:string,password:string){
  const ticket=generation,header=await database.header(owner)
  if(!header)throw new Error('本机没有这个账号的离线资料')
  const key=await unlockHeader(header,password)
  if(ticket!==generation)throw new DOMException('解锁已取消','AbortError')
  // Verify the workspace authentication tag before exposing a capability.
  const probe:VaultCapability={owner,generation:ticket,key,signal:controller.signal,check(){if(ticket!==generation)throw new DOMException('解锁已取消','AbortError')}}
  const row=await database.read(probe,'workspace')
  if(!row)throw new Error('本机离线库不完整，不能解锁')
  decode(await open(key,owner,'records','workspace',row));probe.check()
  return activate(owner,key)
}
export async function createVault(owner:string,password:string,verify:()=>Promise<void>){
  const ticket=authSession.ticket();await verify()
  const check=()=>{if(!authSession.current(ticket,owner))throw new Error('创建离线库前需要核对当前服务器账号')}
  check();const {header,key}=await createHeader(owner,password);check()
  const row={...await seal(key,owner,'records','workspace',encode(emptyWorkspace())),id:`${owner}:workspace`,owner,version:1};check()
  await database.initialize(header,row,check);check();return activate(owner,key)
}
export async function clearVault(owner:string,unsynced?:number){
  if(current?.owner===owner)lockVault()
  channel?.postMessage({type:'lock',owner})
  try{await database.clear(owner);if(state.failedClearOwner===owner){state.warning='';state.failedClearOwner=null}notify()}
  catch(error){state.failedClearOwner=owner;state.warning=`账号 ${owner.slice(0,8)} 的本机离线资料清除失败，${unsynced===undefined?'未同步修改':`${unsynced} 项未同步操作`}可能仍在设备上。请重试清除。`;notify();throw error}
}
channel?.addEventListener('message',event=>{if(event.data?.type==='lock'&&event.data.owner===current?.owner)lockVault()})
let previous=authSession.snapshot(),explicitLogoutOwner:string|null=null
authSession.subscribe(()=>{
  const next=authSession.snapshot(),old=previous;previous=next
  if(next.phase==='closing'){explicitLogoutOwner=next.owner||old.owner;lockVault();if(explicitLogoutOwner)channel?.postMessage({type:'lock',owner:explicitLogoutOwner})}
  else if(old.phase==='closing'&&next.phase==='anonymous'){
    const owner=explicitLogoutOwner;explicitLogoutOwner=null;lockVault();if(owner)void clearVault(owner).catch(()=>{})
  }else if((old.phase==='active'&&next.phase==='anonymous')||(next.phase==='active'&&next.owner!==old.owner)||(next.phase==='active'&&current&&next.owner!==current.owner)||(old.owner!==next.owner&&old.owner!==null))lockVault()
})
export const vaultSnapshot=()=>state
export const subscribeVault=(fn:()=>void)=>{listeners.add(fn);return()=>{listeners.delete(fn)}}
