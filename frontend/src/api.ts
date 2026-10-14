import type { components } from './api.generated'
export type User = components['schemas']['UserOut']
export type Invite = components['schemas']['InviteOut']
export type CreatedInvite = components['schemas']['CreatedInvite']
export type Job = components['schemas']['JobOut']
export type Notice = components['schemas']['NotificationOut']
export type Audit = components['schemas']['AuditOut']
export type LoginInput = components['schemas']['LoginIn']
export type RegisterInput = components['schemas']['RegisterIn']
export type ResetInput = components['schemas']['ResetIn']
export class ApiError extends Error {
  constructor(public status:number, public code:string, message:string, public requestId:string) {super(message)}
}
export async function api<T>(path:string, method='GET', body?:unknown):Promise<T> {
  const headers:Record<string,string> = {}
  if (body !== undefined) headers['Content-Type']='application/json'
  if (method !== 'GET') {
    const r = await fetch('/api/v1/auth/csrf',{credentials:'same-origin'})
    if (!r.ok) throw new Error('无法连接服务，请稍后重试')
    const data:components['schemas']['CsrfOut'] = await r.json()
    headers['X-CSRF-Token']=data.csrf_token
  }
  const response = await fetch('/api/v1'+path,{method,headers,credentials:'same-origin',body:body === undefined ? undefined : JSON.stringify(body)})
  const data=await response.json()
  if (!response.ok) throw new ApiError(response.status,data.code,data.message,data.request_id)
  return data as T
}
export const date = (s:string) => new Date(s).toLocaleString('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})
