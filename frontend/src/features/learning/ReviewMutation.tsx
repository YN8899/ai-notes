import {useEffect,useRef,useState} from 'react'
import {useQueryClient} from '@tanstack/react-query'
import {api,ApiError} from '../../api'
import {authSession,readSession,sessionEndedEvent} from '../../session'
import {NoteError} from '../notes/NoteError'
import {ambiguousReview,attemptMatches,type Receipt,type ReviewAttempt,type ReviewLogs,type ReviewPlan} from './reviews'

// Pending writes survive navigation/reload, but never an account boundary, even
// when the review screen was already unmounted before logout.
function clearReviewAttempts(){try{for(const key of Object.keys(sessionStorage))if(key.startsWith('ai-notes:review-attempt:v1:'))sessionStorage.removeItem(key)}catch{/* Credentials and screen are still invalidated by authSession. */}}
let previousSession=authSession.ticket()
authSession.subscribe(()=>{const next=authSession.ticket();if(next.phase==='closing'||next.phase==='anonymous'||previousSession.owner&&previousSession.owner!==next.owner)clearReviewAttempts();previousSession=next})
window.addEventListener(sessionEndedEvent,clearReviewAttempts)

function storageKey(owner:string){return 'ai-notes:review-attempt:v1:'+owner+':'+(readSession()?.scope||'unavailable')}
function read(key:string):ReviewAttempt|null {try{const value=JSON.parse(sessionStorage.getItem(key)||'null');return attemptMatches(value)?value:null}catch{return null}}
export function useReviewMutation(owner:string,sessionId:string,onSuccess?:(receipt:Receipt)=>void){
  const client=useQueryClient(),[ticket]=useState(()=>authSession.ticket()),[key]=useState(()=>storageKey(owner)),[attempt,setAttempt]=useState<ReviewAttempt|null>(()=>read(key)),[busy,setBusy]=useState(false),[uncertain,setUncertain]=useState(()=>!!read(key)),[error,setError]=useState<unknown>(null),[notice,setNotice]=useState(''),lock=useRef(false)
  const active=()=>authSession.current(ticket,owner)&&ticket.id===sessionId
  function persist(value:ReviewAttempt|null){try{if(value)sessionStorage.setItem(key,JSON.stringify(value));else sessionStorage.removeItem(key);return true}catch{return false}}
  useEffect(()=>{const clear=()=>{try{sessionStorage.removeItem(key)}catch{/* Screen still locks when storage is unavailable. */}};const unsubscribe=authSession.subscribe(()=>{if(!active())clear()});window.addEventListener(sessionEndedEvent,clear);return()=>{unsubscribe();window.removeEventListener(sessionEndedEvent,clear)}},[key])
  function refresh(){void client.invalidateQueries({queryKey:['reviews',owner,sessionId]})}
  function success(receipt:Receipt,message:string){persist(null);setAttempt(null);setUncertain(false);setError(null);setNotice(message);refresh();onSuccess?.(receipt)}
  async function send(value:ReviewAttempt,retry=false){if(lock.current||!active()||attempt&&!retry)return;if(!retry&&!persist(value)){setError(new Error('浏览器无法保存原始请求，尚未提交。请允许当前标签页存储。'));return}lock.current=true;setAttempt(value);setBusy(true);setError(null);setNotice('');try{const receipt=await api<Receipt>(value.path,value.method,value.body);if(active())success(receipt,value.path.endsWith('/practice')?'临时练习已记录，不改变下次时间、计划版本或算法状态。':'已保存，排期与历史已更新。')}catch(e){if(active()){setError(e);setUncertain(ambiguousReview(e));if(!ambiguousReview(e)){persist(null);setAttempt(null);if(e instanceof ApiError&&e.status===409)setNotice(value.path==='/learning/review-plans'?'未重复建立计划。请打开复习中心查看当前对象的有效计划。':'计划已变化。输入已保留，请先读取当前计划，再明确采用新版本。')}}}finally{lock.current=false;if(active())setBusy(false)}}
  async function reconcile(){if(lock.current||!attempt||!active())return;lock.current=true;setBusy(true);setError(null);try{const p=new URLSearchParams({limit:'50',offset:'0'});if(attempt.planId)p.set('plan_id',attempt.planId);else if(attempt.itemId)p.set('item_id',attempt.itemId);let found:ReviewLogs['items'][number]|undefined;for(let start=0;start<=100000;start+=50){p.set('offset',String(start));const logs=await api<ReviewLogs>('/learning/review-logs?'+p);if(!active())return;found=logs.items.find(log=>log.request_key===attempt.body.request_key);if(found||start+50>=logs.total)break}if(found){const plan=found.plan_id?await api<ReviewPlan>('/learning/review-plans/'+found.plan_id):null;if(active())success({log:found,plan},'已找到原请求的历史记录，没有再次写入。')}else if(active())setNotice('当前历史中未找到原请求。可明确重试原编号与原参数；不会自动发起新评价。')}catch(e){if(active())setError(e)}finally{lock.current=false;if(active())setBusy(false)}}
  const feedback=<><NoteError error={error}/>{notice?<p role="status">{notice}</p>:null}{uncertain&&attempt?<div className="review-uncertain" role="alert"><p>原请求结果不确定，可能已经保存。原编号、参数和版本已保留，刷新后也不会自动重发。</p><small>请求编号：{attempt.body.request_key}</small><div className="card-actions"><button type="button" className="secondary" disabled={busy} onClick={()=>void reconcile()}>核对原请求（只读）</button><button type="button" className="secondary" disabled={busy} onClick={()=>void send(attempt,true)}>重试原编号与原参数</button></div></div>:null}</>
  return {send,busy,blocked:busy||uncertain,feedback,setError,attempt,uncertain}
}
