import { ApiError } from '../../api'
import type { UploadOut } from './types'

export async function sendContent(upload:UploadOut,file:File,progress:(percent:number)=>void,signal:AbortSignal):Promise<UploadOut>{
  const target=new URL(upload.upload_url,window.location.origin)
  if(target.origin!==window.location.origin||target.pathname!==`/api/v1/uploads/${upload.id}/content`||upload.upload_method!=='PUT')throw new Error('上传地址不符合私有访问规则')
  const response=await fetch('/api/v1/auth/csrf',{credentials:'same-origin',signal})
  if(!response.ok)throw new Error('无法获取上传授权，请重试')
  const csrf:{csrf_token:string}=await response.json()
  signal.throwIfAborted()
  return new Promise((resolve,reject)=>{
    const request=new XMLHttpRequest(),abort=()=>request.abort()
    request.open('PUT',target.pathname+target.search)
    request.setRequestHeader('Content-Type',file.type)
    request.setRequestHeader('X-CSRF-Token',csrf.csrf_token)
    request.upload.onprogress=e=>{if(e.lengthComputable)progress(Math.round(e.loaded/e.total*100))}
    request.onload=()=>{try{const data=JSON.parse(request.responseText);if(request.status>=200&&request.status<300)resolve(data as UploadOut);else reject(new ApiError(request.status,data.code||'upload_failed',data.message||'文件上传失败',data.request_id||''))}catch{reject(new Error('上传服务返回无效响应，请重试'))}}
    request.onerror=()=>reject(new Error('网络中断，文件尚未上传完成，请重试'))
    request.onabort=()=>reject(new DOMException('已停止上传','AbortError'))
    request.onloadend=()=>signal.removeEventListener('abort',abort)
    signal.addEventListener('abort',abort,{once:true})
    request.send(file)
  })
}
