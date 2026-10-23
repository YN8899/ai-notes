import { createUuid } from './uuid.ts'

const storageKey = 'ai-notes:tab-session:v1'
export const sessionEndedEvent = 'ai-notes:session-ended'
export interface TabSession { session_token:string; csrf_token:string; scope:string }

// The storage is injectable so tests exercise two independent browser tabs.
export function createSessionStore(storage:Pick<Storage,'getItem'|'setItem'|'removeItem'>) {
  return {
    read():TabSession|null {
      const raw=storage.getItem(storageKey)
      if(!raw)return null
      try {
        const value=JSON.parse(raw)
        if(value&&typeof value.session_token==='string'&&value.session_token.length>=20&&typeof value.csrf_token==='string'&&value.csrf_token.length>=20&&typeof value.scope==='string')return value
      } catch { /* Discard invalid local state without logging credentials. */ }
      storage.removeItem(storageKey)
      return null
    },
    save(value:TabSession){storage.setItem(storageKey,JSON.stringify(value))},
    clear(){storage.removeItem(storageKey)},
  }
}

function store(){return createSessionStore(window.sessionStorage)}
export function readSession(){return store().read()}
export function saveSession(value:Omit<TabSession,'scope'>){store().save({...value,scope:createUuid()})}
export function clearSession(expectedToken?:string){
  if(expectedToken&&readSession()?.session_token!==expectedToken)return
  store().clear()
  window.dispatchEvent(new Event(sessionEndedEvent))
}
export function sessionHeaders(session:TabSession):Record<string,string>{
  return {'Authorization':'Bearer '+session.session_token,'X-CSRF-Token': session.csrf_token}
}

let initialization:Promise<void>|undefined
export function shouldRestoreSession(navigationType:string|undefined){return navigationType==='reload'}
// Browsers may copy sessionStorage when opening/duplicating a tab. The new
// document discards a copied session; it must not revoke the original one.
// Broadcast only a random scope, never credentials or personal data.
export function initializeTabSession():Promise<void>{
  if(initialization)return initialization
  initialization=new Promise<void>(resolve=>{
    // A fresh navigation (including an opener/duplicated history entry) must
    // not inherit copied credentials. Only an explicit reload restores them.
    const navigation=performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming|undefined
    if(!shouldRestoreSession(navigation?.type))store().clear()
    if(typeof BroadcastChannel==='undefined'){
      resolve();return
    }
    const channel=new BroadcastChannel('ai-notes:tab-ownership:v1')
    const claim=createUuid(),started=performance.timeOrigin
    let active=false
    channel.onmessage=({data})=>{
      const current=readSession()
      if(!current||!data||data.scope!==current.scope)return
      const earlier=typeof data.started==='number'&&(data.started<started||(data.started===started&&data.claim<claim))
      if(data.type==='claim'&&data.claim!==claim&&(active||!earlier)){
        channel.postMessage({type:'claimed',scope:current.scope,target:data.claim,claim,started})
      }else if(data.type==='claimed'&&data.target===claim&&earlier){clearSession(current.session_token)}
    }
    const current=readSession()
    if(!current){active=true;resolve();return}
    channel.postMessage({type:'claim',scope:current.scope,claim,started})
    setTimeout(()=>{active=true;resolve()},150)
  })
  return initialization
}
