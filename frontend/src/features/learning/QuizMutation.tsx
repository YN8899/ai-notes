import {useEffect,useRef,useState} from 'react'
import {api,ApiError} from '../../api'
import {authSession,readSession,sessionEndedEvent} from '../../session'
import {NoteError} from '../notes/NoteError'
import {pendingKey,type Pending} from './quizzes'
// The module stays subscribed even when the workspace is closed before logout.
function clearQuizStorage(){try{for(const key of Object.keys(sessionStorage))if(key.startsWith('ai-notes:quiz-pending:v1:'))sessionStorage.removeItem(key)}catch{/* The session barrier still locks the UI when storage is unavailable. */}}
let previousSession=authSession.ticket()
authSession.subscribe(()=>{const next=authSession.ticket();if(next.phase==='closing'||next.phase==='anonymous'||previousSession.owner&&previousSession.owner!==next.owner)clearQuizStorage();previousSession=next})
window.addEventListener(sessionEndedEvent,clearQuizStorage)
/** Persist exact operations before sending. Mount/refresh never replays a write. */
export function useQuizMutation<T>(owner:string,sessionId:string,channel:string,onSuccess:(data:T,pending:Pending,retry:boolean)=>void){
  const [ticket]=useState(()=>authSession.ticket()),key=pendingKey(owner,readSession()?.scope||sessionId,channel),mounted=useRef(true),lock=useRef(false)
  const [pending,setPending]=useState<Pending|null>(()=>{try{return JSON.parse(sessionStorage.getItem(key)||'null')}catch{return null}}),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[notice,setNotice]=useState(''),[read,setRead]=useState(false)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[])
  const active=()=>mounted.current&&authSession.current(ticket,owner)&&ticket.id===sessionId
  function persist(value:Pending|null){if(value)sessionStorage.setItem(key,JSON.stringify(value));else sessionStorage.removeItem(key);setPending(value)}
  async function send(value:Pending,retry=false){if(lock.current||!active()||pending&&!retry)return;lock.current=true;setBusy(true);setError(null);setNotice('');setRead(false)
    try{persist(value);const data=await api<T>(value.path,value.method,value.body);if(active()){persist(null);onSuccess(data,value,retry);setNotice('已保存。')}}catch(e){if(active()){setError(e);setNotice(e instanceof ApiError&&e.status===409?'版本冲突。输入和原请求已保留，请先读取服务器版本核对。':'原请求结果未确认，保留原编号和参数；不会自动重发。')}}finally{lock.current=false;if(active())setBusy(false)}}
  async function reconcile(action:()=>Promise<unknown>){if(lock.current||!active())return;lock.current=true;setBusy(true);setError(null);try{await action();if(active()){setRead(true);setNotice('已只读核对服务器状态。请核对你的输入与保存结果，再明确处理原请求。')}}catch(e){if(active())setError(e)}finally{lock.current=false;if(active())setBusy(false)}}
  const feedback=<><NoteError error={error}/>{notice?<p role="status">{notice}</p>:null}{pending?<div className="quiz-warning" role="alert"><p>有原请求待核对。刷新和恢复网络只读取，不会自动写入。</p>{channel==='generation'||channel.startsWith('grading:')?<p>解除本机阻塞不会取消服务器原调用；再次明确确认 AI 操作可能产生另一笔费用。</p>:null}<small>原请求：{String(pending.body.request_key||'')} · {pending.path}</small><div className="card-actions"><button type="button" className="secondary" disabled={busy||error instanceof ApiError&&error.status===409} onClick={()=>void send(pending,true)}>重试原编号与原参数</button>{read?<button type="button" className="text-button" disabled={busy} onClick={()=>{try{persist(null);setError(null);setNotice('已解除本机待处理标记，保留当前输入；这不会取消服务器原请求。')}catch(e){setError(e)}}}>已核对，结束原请求并保留输入</button>:null}</div></div>:null}</>
  return {pending,busy,blocked:busy||!!pending,send,reconcile,feedback,active}
}
