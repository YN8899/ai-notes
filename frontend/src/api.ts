import type { components } from './api.generated'
import { clearSession,initializeTabSession,readSession,saveSession,sessionHeaders,type TabSession } from './session.ts'
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
  status:number
  code:string
  requestId:string
  constructor(status:number, code:string, message:string, requestId:string) {super(message);this.status=status;this.code=code;this.requestId=requestId}
}
export async function authContext(){
  await initializeTabSession()
  const session=readSession()
  if(!session)throw new ApiError(401,'login_required','请先登录','')
  return {session,headers:sessionHeaders(session)}
}
export function rejectExpiredSession(response:Response,session:TabSession|null){
  if(response.status===401&&session)clearSession(session.session_token)
}
async function sendAuthorized(path:string,init:RequestInit,context:Awaited<ReturnType<typeof authContext>>){
  const {session}=context,headers=new Headers(init.headers)
  for(const [name,value] of Object.entries(context.headers))headers.set(name,value)
  const response=await fetch('/api/v1'+path,{...init,headers,credentials:'omit',redirect:'error'})
  rejectExpiredSession(response,session)
  return response
}
export async function apiResponse(path:string,init:RequestInit={}):Promise<Response>{
  return sendAuthorized(path,init,await authContext())
}
export async function logoutTab():Promise<string>{
  const context=await authContext()
  const response=await sendAuthorized('/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'},context)
  if(!response.ok){const data=await response.json();throw new ApiError(response.status,data.code,data.message,data.request_id)}
  return context.session.session_token
}
export async function api<T>(path:string, method='GET', body?:unknown):Promise<T> {
  let response:Response
  const headers:Record<string,string> = body===undefined?{}:{'Content-Type':'application/json'}
  const publicAuth=['/auth/tab/login','/auth/tab/register','/auth/tab/password/reset'].includes(path)
  if(publicAuth){
    await initializeTabSession()
    // Verify that storage is available before creating a server-side session.
    const probe='ai-notes:storage-probe';sessionStorage.setItem(probe,'1');sessionStorage.removeItem(probe)
    headers['X-Notes-Client']='tab'
    response=await fetch('/api/v1'+path,{method,headers,credentials:'omit',redirect:'error',body:JSON.stringify(body)})
  }else{
    response=await apiResponse(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body)})
  }
  if (response.status===204) return undefined as T
  const data=await response.json()
  if (!response.ok) throw new ApiError(response.status,data.code,data.message,data.request_id)
  return data as T
}
export async function authenticate(path:'/auth/tab/login'|'/auth/tab/register',body:LoginInput|RegisterInput):Promise<User>{
  const result=await api<components['schemas']['TabSessionOut']>(path,'POST',body)
  saveSession({session_token:result.session_token,csrf_token:result.csrf_token})
  return result.user
}
export const date = (s:string) => new Date(s).toLocaleString('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})
