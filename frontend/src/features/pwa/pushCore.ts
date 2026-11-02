export type PushStatus={id:string;enabled:boolean}
export type Binding={id:string;owner:string;scope:string}
export type PushApi=<T>(path:string,method?:string,body?:unknown)=>Promise<T>
export type PushTestReceipt={id:string;kind:string;status:string}

export function pushTestRequestKey(storage:Pick<Storage,'getItem'|'setItem'>,scope:string,subscriptionId:string,create:()=>string){
  const slot='ai-notes:push-test:v1:'+scope+':'+subscriptionId
  let key=storage.getItem(slot)
  if(!key){key=create();storage.setItem(slot,key)}
  return key
}

export function canTestPush(binding:Binding|null,owner:string,scope:string,config:PushConfig|undefined,rows:PushStatus[]|undefined){
  return Boolean(config?.enabled&&config.publicVapid&&binding?.owner===owner&&binding.scope===scope&&rows?.some(row=>row.id===binding.id&&row.enabled))
}

export async function sendTestPush(options:{binding:Binding|null;owner:string;scope:string;config:PushConfig;rows:PushStatus[];requestKey:string;api:PushApi;check:()=>void}){
  options.check()
  if(!canTestPush(options.binding,options.owner,options.scope,options.config,options.rows))throw new Error('请先在当前会话开启并关联本设备推送。')
  const receipt=await options.api<PushTestReceipt>('/push/test','POST',{request_key:options.requestKey,subscription_id:options.binding!.id})
  options.check()
  return receipt
}

export async function boundedPushCleanup(cleanup:(signal:AbortSignal)=>Promise<unknown>,milliseconds=2000){
  const controller=new AbortController()
  let timer:ReturnType<typeof setTimeout>|undefined
  const deadline=new Promise<void>(resolve=>{timer=setTimeout(()=>{controller.abort();resolve()},milliseconds)})
  const pending=Promise.resolve().then(()=>cleanup(controller.signal)).catch(()=>{})
  try{await Promise.race([pending,deadline])}finally{clearTimeout(timer);controller.abort()}
}

export function applicationKey(value:string){
  const raw=atob(value.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-value.length%4)%4))
  return Uint8Array.from(raw,c=>c.charCodeAt(0))
}

/** A shared browser subscription can belong to a DIFFERENT independent tab.
 * Never unsubscribe on config mismatch, 409, failed registration or permission.
 */
export type PushConfig={enabled:boolean;publicVapid:string|null}
export async function enablePush(options:{registration:ServiceWorkerRegistration;config:PushConfig;permission:()=>Promise<NotificationPermission>;api:PushApi;check:()=>void}){
  const config=options.config
  options.check()
  if(!config.enabled||!config.publicVapid)throw new Error('服务尚未配置浏览器推送，站内提醒仍可使用。')
  if(await options.permission()!=='granted')throw new Error('未获得通知权限，站内提醒仍可使用。可在浏览器设置中调整权限。')
  options.check()
  let subscription=await options.registration.pushManager.getSubscription()
  options.check()
  if(!subscription){
    subscription=await options.registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:applicationKey(config.publicVapid)})
    options.check()
  }
  // A 409 leaves the existing browser subscription untouched, including A's
  // subscription when B is logged into this tab, even after a VAPID rotation.
  const claimed=await options.api<PushStatus>('/push/subscriptions','PUT',subscription.toJSON())
  options.check()
  const previousKey=subscription.options?.applicationServerKey
  const wanted=applicationKey(config.publicVapid)
  if(previousKey){
    const previous=new Uint8Array(previousKey)
    if(previous.length!==wanted.length||previous.some((value,index)=>value!==wanted[index])){
      // Ownership is now proven by the successful session-scoped PUT. This
      // explicit enable gesture may safely replace our own old-key object.
      await subscription.unsubscribe()
      options.check()
      subscription=await options.registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:wanted})
      options.check()
      const fresh=await options.api<PushStatus>('/push/subscriptions','PUT',subscription.toJSON())
      options.check()
      if(fresh.id!==claimed.id)await options.api('/push/subscriptions/'+claimed.id,'DELETE')
      return fresh
    }
  }
  return claimed
}

export async function disableOwnedPush(options:{binding:Binding|null;owner:string;scope:string;api:PushApi;registration:ServiceWorkerRegistration|null;check:()=>void}){
  const {binding}=options
  if(!binding||binding.owner!==options.owner||binding.scope!==options.scope)return false
  const rows=await options.api<PushStatus[]>('/push/subscriptions')
  options.check()
  if(!rows.some(row=>row.id===binding.id))return false
  // GET proves this id belongs to the current server session. Keep the server
  // binding until the browser object is cancelled, preventing another account
  // from acquiring this endpoint during the browser unsubscribe operation.
  const subscription=await options.registration?.pushManager.getSubscription()
  options.check()
  if(subscription)await subscription.unsubscribe()
  options.check()
  await options.api('/push/subscriptions/'+binding.id,'DELETE')
  return true
}
