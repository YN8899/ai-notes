import {useEffect,useState} from 'react'
import {apiResponse,ApiError} from '../../api'
import {authSession,SessionChangedError} from '../../session'

export async function readAsset(path:string,signal?:AbortSignal){
  if(!path.startsWith('/social/')&&!path.startsWith('/community/'))throw new Error('文件路径无效')
  const ticket=authSession.ticket(),response=await apiResponse(path,{signal})
  if(!response.ok){const data=await response.json();throw new ApiError(response.status,data.code,data.message,data.request_id)}
  const blob=await response.blob()
  if(!authSession.current(ticket))throw new SessionChangedError()
  return blob
}
export function ProtectedImage({path,alt}:{path:string;alt:string}){
  const [url,setUrl]=useState(''),[error,setError]=useState(''),[retry,setRetry]=useState(0)
  useEffect(()=>{const controller=new AbortController();let alive=true,objectUrl='';setUrl('');setError('');void readAsset(path,controller.signal).then(blob=>{if(alive){objectUrl=URL.createObjectURL(blob);setUrl(objectUrl)}}).catch(error=>{if(alive)setError(error instanceof Error?error.message:'图片读取失败')});return()=>{alive=false;controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)}},[path,retry])
  return <figure className="social-image">{error?<div role="alert">{error}<button className="text-button" onClick={()=>setRetry(v=>v+1)}>重新读取图片</button></div>:url?<img src={url} alt={alt} loading="lazy"/>:<p role="status">正在读取图片…</p>}<figcaption>{alt}</figcaption></figure>
}
export async function downloadAsset(path:string,name:string){const blob=await readAsset(path);const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
