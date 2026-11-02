import {authContext,rejectExpiredSession} from '../../api.ts'
import {authSession} from '../../session.ts'
import {lockVault} from './vault.ts'
import type {Current,VaultCapability} from './types.ts'
export class OfflineHttpError extends Error{
  readonly status:number
  readonly code:string
  readonly current:Current|null
  constructor(status:number,code:string,message:string,current:Current|null=null){super(message);this.status=status;this.code=code;this.current=current}
}
export async function onlineContext(cap:VaultCapability){
  cap.check();const context=await authContext();cap.check();context.check()
  const controller=new AbortController(),unsubscribe=authSession.subscribe(()=>{if(!authSession.current(context.ticket))controller.abort()})
  const signal=AbortSignal.any([cap.signal,controller.signal])
  const check=()=>{cap.check();context.check();signal.throwIfAborted()}
  async function response(path:string,init:RequestInit={}){
    check();if(context.ticket.phase==='closing')throw new Error('正在退出，不能同步')
    if(!path.startsWith('/')||path.startsWith('//'))throw new Error('离线同步只允许本站接口')
    const headers=new Headers(init.headers);for(const [key,value] of Object.entries(context.headers))headers.set(key,value)
    const r=await fetch('/api/v1'+path,{...init,headers,signal,credentials:'omit',cache:'no-store',redirect:'error'});check();rejectExpiredSession(r,context.session);check();return r
  }
  async function json<T>(path:string,method='GET',body?:unknown):Promise<T>{
    const r=await response(path,{method,headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
    if(r.status===204)return undefined as T
    const data=await r.json();check();if(!r.ok)throw new OfflineHttpError(r.status,String(data.code||'request_failed'),String(data.message||'同步请求失败'),data.current||null);return data as T
  }
  try{
    const me=await json<{id:string}>('/auth/me');check()
    if(me.id!==cap.owner){lockVault();throw new Error('服务器账号与本机离线库不一致，已锁定；未发送任何修改')}
    return {check,response,json,close:unsubscribe}
  }catch(error){unsubscribe();throw error}
}
export type OnlineContext=Awaited<ReturnType<typeof onlineContext>>
