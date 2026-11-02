import {api,pushLogoutApi} from '../../api'
import {authSession,readSession,SessionChangedError} from '../../session.ts'
import {boundedPushCleanup,disableOwnedPush,enablePush,sendTestPush,pushTestRequestKey,type Binding,type PushApi,type PushConfig,type PushStatus} from './pushCore.ts'
import {pwaSnapshot} from './register'
import {createUuid} from '../../uuid.ts'

const key='ai-notes:push-binding:v1'
export function readBinding():Binding|null{
  try{const row=JSON.parse(window.localStorage.getItem(key)||'null');return row&&typeof row.id==='string'&&typeof row.owner==='string'&&typeof row.scope==='string'?row:null}catch{return null}
}
export function pushSupported(){return window.isSecureContext&&'serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window}
async function withBrowserLock<T>(run:()=>Promise<T>){
  return navigator.locks?navigator.locks.request('ai-notes:browser-push',run):run()
}
export async function enableBrowserPush(owner:string,config:PushConfig){
  if(!pushSupported())throw new Error('此浏览器暂不支持推送；iPhone / iPad 请先添加到主屏幕后打开。站内提醒仍可使用。')
  if(!config.enabled||!config.publicVapid)throw new Error('服务尚未配置推送，站内提醒仍可使用。')
  // Trigger permission directly in the user gesture, before ANY awaited network
  // request or cross-tab lock; Safari loses activation across those awaits.
  const permission=Notification.requestPermission()
  return withBrowserLock(()=>enable(owner,config,permission))
}
async function enable(owner:string,config:PushConfig,permission:Promise<NotificationPermission>){
  if(!pushSupported())throw new Error('此浏览器暂不支持推送；iPhone / iPad 请先添加到主屏幕后打开。站内提醒仍可使用。')
  const ticket=authSession.ticket(),scope=readSession()?.scope
  const check=()=>{if(!authSession.current(ticket,owner)||readSession()?.scope!==scope)throw new SessionChangedError()}
  check()
  const registration=pwaSnapshot().registration
  if(!registration)throw new Error('推送需要已部署的 HTTPS 应用及离线应用壳，请稍后重试。')
  const result=await enablePush({registration,config,api,check,permission:()=>permission})
  check()
  window.localStorage.setItem(key,JSON.stringify({id:result.id,owner,scope}))
  return result
}
export async function disableBrowserPush(owner:string){return withBrowserLock(()=>disable(owner))}
export async function sendBrowserPushTest(owner:string,config:PushConfig){
  const ticket=authSession.ticket(),scope=readSession()?.scope||''
  const check=()=>{if(!authSession.current(ticket,owner)||readSession()?.scope!==scope)throw new SessionChangedError()}
  check()
  const binding=readBinding()
  if(!binding||binding.owner!==owner||binding.scope!==scope)throw new Error('请先在当前会话关联本设备推送。')
  // Persist before the POST so a lost response/remount/retry reuses the same
  // idempotency key. A new device association gets its own explicit test.
  const requestKey=pushTestRequestKey(window.sessionStorage,scope,binding.id,createUuid)
  const rows=await api<PushStatus[]>('/push/subscriptions')
  check()
  return sendTestPush({binding,owner,scope,config,rows,requestKey,api,check})
}
export async function cancelPushForLogout(owner:string){
  // Browser dialogs, cross-tab locks, or network failures must never prevent
  // server logout. Aborting cleanup also prevents delayed mutations afterwards.
  await boundedPushCleanup(signal=>withBrowserLock(()=>disable(owner,(path,method)=>pushLogoutApi(path,method,signal),true,signal)))
}
async function disable(owner:string,request:PushApi=api,closing=false,signal?:AbortSignal){
  const ticket=authSession.ticket(),scope=readSession()?.scope||''
  const check=()=>{if(signal?.aborted||!authSession.current(ticket)||ticket.owner!==owner||(!closing&&ticket.phase!=='active')||readSession()?.scope!==scope)throw new SessionChangedError()}
  check()
  const result=await disableOwnedPush({binding:readBinding(),owner,scope,api:request,check,registration:pwaSnapshot().registration})
  check()
  if(result)window.localStorage.removeItem(key)
  return result
}
