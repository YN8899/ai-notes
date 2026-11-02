import type {QueryClient} from '@tanstack/react-query'
import {api} from '../../api'
import {authSession} from '../../session'

// One connection per document/session, shared by the app shell and chat view.
// No cross-tab BroadcastChannel: each tab owns a separate server auth session.
type Entry={key:string;retain:(listener:(status:string)=>void)=>()=>void;close:()=>void}
let active:Entry|undefined

export function subscribeRealtime(owner:string,client:QueryClient,listener:(status:string)=>void){
  const ticket=authSession.ticket(),key=`${ticket.id}:${ticket.generation}:${owner}`
  if(!owner||!authSession.current(ticket,owner))return()=>{}
  if(active?.key!==key){active?.close();active=create(owner,key,client)}
  return active.retain(listener)
}

function create(owner:string,key:string,client:QueryClient):Entry{
  const ticket=authSession.ticket(),listeners=new Set<(value:string)=>void>()
  let closed=false,connecting=false,socket:WebSocket|undefined,timer:ReturnType<typeof setTimeout>|undefined,attempt=0,status='正在连接实时消息…'
  const current=()=>!closed&&authSession.current(ticket,owner)
  const setStatus=(next:string)=>{status=next;listeners.forEach(fn=>fn(next))}
  const invalidate=(domains:string[])=>{
    if(domains.includes('chat'))void client.invalidateQueries({queryKey:['social',owner,'chat']})
    if(domains.includes('notifications'))void client.invalidateQueries({queryKey:['notices',owner]})
  }
  function retry(){
    if(!current())return
    clearTimeout(timer)
    setStatus(navigator.onLine?'实时连接暂不可用，正在自动重连；持久消息会通过 HTTP 自动补拉。':'网络已断开；恢复联网后会自动补拉持久消息和通知。')
    timer=setTimeout(()=>void connect(),Math.min(30000,1000*2**Math.min(attempt++,5)))
  }
  async function connect(){
    if(!current()||connecting)return
    connecting=true;clearTimeout(timer)
    try{
      const result=await api<{ticket:string}>('/social/realtime-ticket','POST',{})
      if(!current())return
      const next=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/v1/realtime/ws`)
      socket=next
      next.onopen=()=>{if(current())next.send(JSON.stringify({ticket:result.ticket}));else next.close()}
      next.onmessage=event=>{
        if(!current()||socket!==next)return
        try{
          const data=JSON.parse(event.data)
          if(!['ready','changed','heartbeat'].includes(data.type))throw new Error('frame')
          if(data.type==='ready')attempt=0
          setStatus(data.realtime===false?'实时提示暂不可用，正在通过 HTTP 自动补拉消息和通知。':'实时消息已连接')
          if(data.type==='ready'||data.resync)invalidate(['chat','notifications'])
          else if(data.type==='changed')invalidate(Array.isArray(data.domains)?data.domains:['chat'])
        }catch{next.close()}
      }
      next.onerror=()=>next.close()
      next.onclose=()=>{if(socket===next)retry()}
    }catch{retry()}finally{connecting=false}
  }
  const unsubscribe=authSession.subscribe(()=>{if(!authSession.current(ticket,owner))close()})
  const online=()=>{if(!current())return;invalidate(['chat','notifications']);if(socket?.readyState!==WebSocket.OPEN){clearTimeout(timer);const old=socket;socket=undefined;old?.close();void connect()}}
  const offline=()=>setStatus('网络已断开；恢复联网后会自动补拉持久消息和通知。')
  window.addEventListener('online',online)
  window.addEventListener('offline',offline)
  function close(){
    if(closed)return
    closed=true;clearTimeout(timer);unsubscribe();socket?.close()
    window.removeEventListener('online',online);window.removeEventListener('offline',offline)
    listeners.clear()
    if(active?.key===key)active=undefined
  }
  void connect()
  return {key,close,retain(fn){listeners.add(fn);fn(status);return()=>{listeners.delete(fn);if(!listeners.size)close()}}}
}
