export type SessionPhase='checking'|'active'|'anonymous'|'closing'
export type SessionTicket={generation:number;id:string;owner:string|null;phase:SessionPhase;until?:number}
export type SessionEvent={id:string;previous:string;owner:string|null;phase:'active'|'anonymous'|'closing';until?:number;bootstrap?:boolean}
export class SessionChangedError extends Error {
  constructor(){super('登录状态已变化，请重新登录后操作。');this.name='AbortError'}
}

// The generation is local to a tab. Wire IDs identify one browser-cookie lifecycle,
// so an old logout completion cannot invalidate a later login by the same owner.
export function createSessionState(publish:(event:SessionEvent)=>void=()=>{},initial?:SessionEvent) {
  const id=()=>crypto.randomUUID()
  let state:SessionTicket={generation:0,id:initial?.id||id(),owner:initial?.owner||null,phase:initial?.phase==='anonymous'?'anonymous':initial?.phase==='closing'?'closing':'checking',until:initial?.until}
  const listeners=new Set<()=>void>()
  const retired=new Set<string>()
  let pendingActive:{owner:string|null;previousId:string}|null=null
  const snapshot=()=>state
  const current=(ticket:SessionTicket,owner?:string)=>ticket.generation===state.generation&&ticket.id===state.id&&(owner===undefined||state.phase==='active'&&state.owner===owner)
  function update(next:Omit<SessionTicket,'generation'>,advance=true){if(next.id!==state.id)retired.add(state.id);state={...next,generation:state.generation+(advance?1:0)};listeners.forEach(listener=>listener())}
  function announce(phase:SessionEvent['phase'],owner:string|null,until?:number){pendingActive=null;const event:SessionEvent={id:id(),previous:state.id,owner,phase,...(until===undefined?{}:{until})};update(event);publish(event);return state}
  return {
    snapshot,ticket:snapshot,current,
    subscribe(listener:()=>void){listeners.add(listener);return()=>{listeners.delete(listener)}},
    login(owner:string,ticket:SessionTicket){if(!current(ticket)||state.phase==='closing')throw new SessionChangedError();return announce('active',owner)},
    verify(owner:string,ticket:SessionTicket){
      if(!current(ticket)||!['checking','active'].includes(state.phase))throw new SessionChangedError()
      const pending=pendingActive;pendingActive=null
      if(pending&&pending.owner!==owner&&state.owner===owner){retired.delete(pending.previousId);update({id:pending.previousId,owner,phase:'active'});return state}
      if(state.owner&&state.owner!==owner)return announce('active',owner)
      const unknown=!state.owner
      update({...state,owner,phase:'active'},false)
      if(unknown)publish({id:state.id,previous:state.id,owner,phase:'active',bootstrap:true})
      return state
    },
    beginLogout(ticket:SessionTicket,until=Date.now()+15000){if(!current(ticket)||state.phase!=='active')throw new SessionChangedError();return announce('closing',state.owner,until)},
    invalidate(ticket:SessionTicket,{broadcast=true}:{broadcast?:boolean}={}){
      if(!current(ticket)||state.phase==='anonymous')return false
      const identified=state.owner!==null
      if(broadcast&&identified)announce('anonymous',null)
      else {pendingActive=null;update({id:id(),owner:null,phase:'anonymous'})}
      return true
    },
    check(){if(state.phase==='active')update({...state,phase:'checking'})},
    receive(event:SessionEvent,authoritative=false){
      if(event.id===state.id||retired.has(event.id))return
      const bootstrap=event.phase==='active'&&(event.bootstrap||event.previous===event.id)
      if(bootstrap&&state.phase==='closing'){publish({id:state.id,previous:event.id,owner:state.owner,phase:'closing',until:state.until});return}
      if(bootstrap&&state.phase==='anonymous')return
      if(event.phase!=='active'&&event.previous!==state.id&&!authoritative)return
      // With storage disabled, concurrent first /me calls can discover the same
      // cookie using different local IDs. A deterministic winner and reply make
      // both tabs share one wire ID before subsequent logout notifications.
      if(bootstrap&&['active','checking'].includes(state.phase)&&state.owner===event.owner&&state.id<event.id){publish({id:state.id,previous:event.id,owner:state.owner,phase:'active',bootstrap:true});return}
      // A channel payload is a reason to verify the cookie, not proof of identity.
      // Keep the verified owner until /me confirms a change, preserving its drafts.
      if(event.phase==='active')pendingActive={owner:event.owner,previousId:pendingActive?.previousId||state.id}
      else pendingActive=null
      update({...event,owner:event.phase==='active'?state.owner:event.owner,phase:event.phase==='active'?'checking':event.phase})
    },
  }
}

const key='ai-notes:session:v1'
function parse(value:unknown):SessionEvent|null {
  if(typeof value!=='object'||value===null)return null
  const event=value as Partial<SessionEvent>
  if(typeof event.id!=='string'||typeof event.previous!=='string'||!(typeof event.owner==='string'||event.owner===null)||!['active','anonymous','closing'].includes(event.phase||''))return null
  if(event.bootstrap!==undefined&&typeof event.bootstrap!=='boolean')return null
  if(event.phase==='closing'&&(!Number.isFinite(event.until)||Number(event.until)>Date.now()+20000))return null
  return event as SessionEvent
}
function readMarker(){try{return parse(JSON.parse(localStorage.getItem(key)||'null'))}catch{return null}}
let channel:BroadcastChannel|null=null,storageWritable=true
export const authSession=createSessionState(event=>{
  try{localStorage.setItem(key,JSON.stringify(event));storageWritable=true}catch{storageWritable=false}
  try{channel?.postMessage({event,persisted:storageWritable})}catch{/* Focus verification remains available. */}
},typeof window==='undefined'?undefined:readMarker()||undefined)

export function receiveSessionEvent(session:ReturnType<typeof createSessionState>,event:SessionEvent,latest:SessionEvent|null,persisted=true){
  if(persisted&&latest&&latest.id!==event.id)return
  session.receive(event,persisted&&latest?.id===event.id)
}

export function startSessionSync(onVerify?:()=>void){
  function receive(value:unknown,persisted=true){const event=parse(value);if(!event)return;if(!persisted)storageWritable=false;const latest=storageWritable?readMarker():null;receiveSessionEvent(authSession,event,latest,persisted)}
  try{channel=new BroadcastChannel(key);channel.onmessage=message=>receive(message.data?.event,message.data?.persisted!==false)}catch{channel=null}
  const storage=(event:StorageEvent)=>{if(event.key===key&&event.newValue){try{receive(JSON.parse(event.newValue))}catch{/* Ignore unrelated or malformed storage. */}}}
  const focus=()=>{const marker=storageWritable?readMarker():null;if(marker)receive(marker);if(navigator.onLine!==false&&['active','checking'].includes(authSession.snapshot().phase)){if(onVerify)onVerify();else authSession.check()}}
  const visible=()=>{if(document.visibilityState==='visible')focus()}
  let timer:ReturnType<typeof setTimeout>|undefined
  const closing=()=>{clearTimeout(timer);const ticket=authSession.ticket();if(ticket.phase==='closing')timer=setTimeout(()=>authSession.invalidate(ticket),Math.max(0,(ticket.until||Date.now())-Date.now()))}
  const unsubscribe=authSession.subscribe(closing);closing()
  window.addEventListener('storage',storage);window.addEventListener('focus',focus);window.addEventListener('online',focus);document.addEventListener('visibilitychange',visible)
  // A tab may have slept through a channel event before the listeners were attached.
  const marker=storageWritable?readMarker():null;if(marker)receive(marker)
  return()=>{unsubscribe();clearTimeout(timer);channel?.close();channel=null;window.removeEventListener('storage',storage);window.removeEventListener('focus',focus);window.removeEventListener('online',focus);document.removeEventListener('visibilitychange',visible)}
}
