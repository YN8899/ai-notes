import type { components } from './api.generated'
import { authSession,SessionChangedError } from './session.ts'
export type User = components['schemas']['UserOut']
export type Invite = components['schemas']['InviteOut']
export type CreatedInvite = components['schemas']['CreatedInvite']
export type Job = components['schemas']['JobOut']
export type Notice = components['schemas']['NotificationOut']
export type Audit = components['schemas']['AuditOut']
export type LoginInput = components['schemas']['LoginIn']
export type RegisterInput = components['schemas']['RegisterIn']
export type ResetInput = components['schemas']['ResetIn']
export type AiCatalog = components['schemas']['AiCatalogOut']
export type AiConnection = components['schemas']['AiConnectionOut']
export class ApiError extends Error {
  status:number;code:string;requestId:string
  constructor(status:number,code:string,message:string,requestId:string){super(message);this.status=status;this.code=code;this.requestId=requestId}
}
export async function api<T>(path:string, method='GET', body?:unknown):Promise<T> {
  const ticket=authSession.ticket()
  const publicAuth=['/auth/login','/auth/register','/auth/password/reset'].includes(path)
  if(publicAuth&&ticket.phase==='closing'||!path.startsWith('/auth/')&&ticket.phase!=='active')throw new SessionChangedError()
  const controller=new AbortController()
  const guard=()=>{if(!authSession.current(ticket))throw new SessionChangedError()}
  const unsubscribe=authSession.subscribe(()=>{if(!authSession.current(ticket))controller.abort()})
  const timeout=path==='/auth/logout'?setTimeout(()=>controller.abort(),12000):undefined
  function unauthorized(){if(!publicAuth&&authSession.current(ticket))authSession.invalidate(ticket)}
  try {
  const headers:Record<string,string> = {}
  if (body !== undefined) headers['Content-Type']='application/json'
  if (method !== 'GET') {
    guard()
    const r = await fetch('/api/v1/auth/csrf',{credentials:'same-origin',signal:controller.signal})
    guard()
    if(r.status===401)unauthorized()
    if (!r.ok) throw new Error('无法连接服务，请稍后重试')
    const data:components['schemas']['CsrfOut'] = await r.json()
    guard()
    headers['X-CSRF-Token']=data.csrf_token
  }
  guard()
  const response = await fetch('/api/v1'+path,{method,headers,credentials:'same-origin',signal:controller.signal,body:body === undefined ? undefined : JSON.stringify(body)})
  guard()
  if (response.status===204) return undefined as T
  const data=await response.json()
  guard()
  if(response.status===401)unauthorized()
  if (!response.ok) throw new ApiError(response.status,data.code,data.message,data.request_id)
  return data as T
  } catch(error) {
    guard()
    throw error
  } finally {unsubscribe();clearTimeout(timeout)}
}
export const date = (s:string) => new Date(s).toLocaleString('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})
