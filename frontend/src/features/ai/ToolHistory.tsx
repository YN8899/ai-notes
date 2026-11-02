import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {api,date} from '../../api'
import type {Note,Version} from '../notes/types'
import {modelLabel} from './model-catalog'
import type {ToolHistory as History,ToolSource,NoteSource} from './tool-contract'
function NoteOrigin({owner,source,onOpenNote}:{owner:string;source:NoteSource;onOpenNote?:(id:string)=>void}){
  const [open,setOpen]=useState(false)
  const original=useQuery({queryKey:['ai-tool-origin',owner,source.note_id,source.revision],enabled:open,retry:false,queryFn:async()=>{
    const note=await api<Note>('/notes/'+source.note_id)
    if(note.owner_id!==owner||note.kind!=='text'||note.deleted_at)throw new Error('原笔记已不存在或不可访问；历史片段仍保留，不提供访问权限。')
    if(note.revision===source.revision)return {note,text:note.plain_text}
    // Read-only bounded lookup. Never infer permission from saved tool text.
    for(let offset=0;offset<1000;offset+=100){const versions=await api<Version[]>('/notes/'+source.note_id+'/versions?limit=100&offset='+offset);const version=versions.find(v=>v.revision===source.revision);if(version)return {note,text:typeof version.snapshot.plain_text==='string'?version.snapshot.plain_text:'（原版本没有文字镜像）'};if(versions.length<100)break}
    throw new Error('未找到该原版本；历史片段仍可阅读，请到本人笔记核对。')
  }})
  return <div className="ai-tool-source"><p>{source.title} · 原版本 {source.revision}{source.start!==undefined?' · 字符范围 '+source.start+'–'+source.end:''}<small>{source.note_id}</small></p><button type="button" className="text-button" onClick={()=>setOpen(v=>!v)}>{open?'收起原版本':'核对本人原笔记 / 原版本'}</button>{open?<>{original.isPending?<p>正在核对来源权限与版本…</p>:original.error?<p className="muted">{original.error.message}</p>:original.data?<><p className="muted">已核对本人原版本 {source.revision}{original.data.note.revision!==source.revision?'；当前笔记已更新':''}。</p><pre>{original.data.text}</pre>{onOpenNote?<button type="button" className="text-button" onClick={()=>onOpenNote(source.note_id)}>打开本人当前原笔记</button>:null}</>:null}</>:null}</div>
}
function Source({owner,source,onOpenNote}:{owner:string;source:ToolSource;onOpenNote?:(id:string)=>void}){
  return 'kind' in source&&source.kind==='learning_summary'?<p>本人学习汇总 · {source.date_from} 至 {source.date_to} · 时区 {source.timezone} · 口径：已记录活动、复习、测验及阅读进度的描述性事实；不推断掌握程度。无记录时样本不足。</p>:<NoteOrigin owner={owner} source={source as NoteSource} onOpenNote={onOpenNote}/>
}
export function ToolHistory({owner,runId,active=false,onOpenNote}:{owner:string;runId:string;active?:boolean;onOpenNote?:(id:string)=>void}){
  const [open,setOpen]=useState(false)
  const history=useQuery({queryKey:['ai-tools-history',owner,runId],queryFn:()=>api<History>(`/ai/runs/${runId}/tools`),enabled:open,retry:false,refetchOnWindowFocus:false,refetchInterval:open&&active?2000:false})
  const value=history.data
  return <details className="ai-tool-history" onToggle={e=>setOpen(e.currentTarget.open)}><summary>工具来源与模型调用记录（只读）</summary>{open?<><button type="button" className="text-button" disabled={history.isFetching} onClick={()=>void history.refetch()}>刷新记录（不会重新调用模型）</button>{history.error?<p className="error" role="alert">{history.error.message}</p>:null}{history.isPending?<p>正在读取记录…</p>:null}{value?<><p>{modelLabel(value.provider,value.model)} · 工具：{value.tools.join('、')||'关闭'}</p>{value.scope?<details><summary>本次冻结授权范围 · {value.scope.notes.length} 篇笔记</summary>{value.scope.notes.map(n=><p key={n.note_id}>{n.title} · 版本 {n.revision}<small>{n.note_id}</small></p>)}{value.scope.learning_summary?<Source owner={owner} source={value.scope.learning_summary}/>:null}</details>:null}<h5>模型调用尝试（顺序）</h5><ol>{value.attempts.map(a=><li key={a.id}>第 {a.position+1} 次 · {a.kind} · {a.status} · {date(a.created_at)}<p>用量：{a.usage?JSON.stringify(a.usage):'未知，仍可能计费'}{a.error_code?' · 拒绝 / 错误：'+a.error_code:''}</p></li>)}</ol><h5>工具执行（顺序）</h5>{!value.calls.length?<p className="muted">暂无工具调用；模型也可能直接回答。</p>:null}<ol>{value.calls.map(call=><li key={call.id}><strong>{call.tool_name} · {call.status}</strong><p>{date(call.created_at)}{call.error_code?' · 拒绝 / 错误：'+call.error_code:''}</p><details><summary>查看工具参数和有界结果</summary><pre>{JSON.stringify(call.arguments,null,2)}</pre><pre>{call.result??'未取得结果。'}</pre></details>{call.sources.map((s,i)=><Source key={i} owner={owner} source={s} onOpenNote={onOpenNote}/>)}</li>)}</ol></>:null}</>:null}</details>
}
