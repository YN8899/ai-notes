import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, date, ApiError, type AiCatalog } from '../../api'
import { active, readStream, type Conversation, type Detail, type Run } from './chat-stream'
import './ai.css'

const labels:Record<Run['status'],string>={queued:'等待生成',running:'正在回复',succeeded:'已完成',cancelled:'已取消',interrupted:'回复中断',failed:'生成失败',truncated:'达到输出上限'}
const errors:Record<string,string>={credential_rejected:'共享模型凭据不可用，请联系管理员。',provider_balance:'共享模型账户余额不足，请联系管理员。',provider_rate_limited:'模型服务繁忙，请稍后主动发送。',provider_unavailable:'模型服务暂时不可用。',provider_unreachable:'未能连接模型服务。',provider_timeout:'模型响应超时。',provider_invalid_response:'模型返回了无法处理的响应。',provider_incomplete:'模型未完整返回结果。',worker_interrupted:'服务曾中断，本次不会自动重新生成。',output_limit:'回复达到长度上限，以下内容不完整。',account_disabled:'账户已停用。',ai_not_configured:'共享模型服务尚未配置。'}
function message(error:unknown) {return error instanceof Error ? error.message : '操作失败，请重新读取'}

export default function AiChat({owner}:{owner:string}) {
  const client=useQueryClient()
  const storageKey=`ai-notes:chat:${owner}`
  const [selected,setSelected]=useState(()=>{try{return sessionStorage.getItem(storageKey)||''}catch{return ''}})
  const [prompt,setPrompt]=useState('')
  const [model,setModel]=useState('deepseek-flash')
  const [streamError,setStreamError]=useState('')
  const [readVersion,setReadVersion]=useState(0)
  const pending=useRef<{conversation:string;key:string;prompt:string;model:string;previous:string|null}|null>(null)
  const newKey=useRef(crypto.randomUUID())
  const sending=useRef(false)
  const conversations=useQuery({queryKey:['ai-conversations',owner],queryFn:()=>api<Conversation[]>('/ai/conversations')})
  const catalog=useQuery({queryKey:['ai-catalog',owner],queryFn:()=>api<AiCatalog>('/ai/providers')})
  const detailKey=['ai-conversation',owner,selected]
  const detail=useQuery({queryKey:detailKey,queryFn:()=>api<Detail>('/ai/conversations/'+selected),enabled:!!selected})
  const latest=detail.data?.runs.at(-1)
  const busy=active(latest)

  function choose(id:string) {
    setSelected(id);setPrompt('');pending.current=null;setStreamError('')
    try{sessionStorage.setItem(storageKey,id)}catch{/* Browser storage is optional. */}
  }
  useEffect(()=>{
    if (!selected && conversations.data?.length) choose(conversations.data[0].id)
  },[conversations.data,selected])

  useEffect(()=>{
    if (!latest || !active(latest)) return
    const controller=new AbortController()
    const id=latest.id
    setStreamError('')
    readStream(id,controller.signal,(name,data)=>{
      client.setQueryData<Detail>(['ai-conversation',owner,selected],old=>{
        if(!old)return old
        return {...old,runs:old.runs.map(run=>{
          if(run.id!==id)return run
          if(name==='meta'||name==='done')return data as Run
          if(name==='state')return {...run,...data as Partial<Run>}
          if(name==='delta') {
            const delta=data as {offset:number;text:string}
            // Python offsets count Unicode code points, including emoji as one.
            if(Array.from(run.answer).length!==delta.offset)throw new Error('回复同步中断，请重新读取')
            return {...run,answer:run.answer+delta.text}
          }
          return run
        })}
      })
    }).catch(error=>{if(!controller.signal.aborted)setStreamError(message(error))})
    return ()=>controller.abort()
  },[latest?.id,busy,owner,selected,readVersion,client])

  const create=useMutation({mutationFn:()=>api<Conversation>('/ai/conversations','POST',{request_key:newKey.current}),onSuccess:row=>{
    newKey.current=crypto.randomUUID();choose(row.id)
    client.setQueryData<Detail>(['ai-conversation',owner,row.id],{...row,runs:[]})
    client.invalidateQueries({queryKey:['ai-conversations',owner]})
  }})
  const send=useMutation({mutationFn:async()=>{
    const previous=latest?.id||null
    if (!pending.current || pending.current.conversation!==selected || pending.current.prompt!==prompt || pending.current.model!==model) {
      pending.current={conversation:selected,key:crypto.randomUUID(),prompt,model,previous}
    }
    const p=pending.current
    return api<Run>(`/ai/conversations/${p.conversation}/runs`,'POST',{request_key:p.key,prompt:p.prompt,model:p.model,previous_run_id:p.previous})
  },onSuccess:run=>{
    pending.current=null;setPrompt('')
    client.setQueryData<Detail>(['ai-conversation',owner,run.conversation_id],old=>old?{...old,title:old.runs.length?old.title:run.prompt.trim().slice(0,64),runs:old.runs.some(r=>r.id===run.id)?old.runs.map(r=>r.id===run.id?run:r):[...old.runs,run]}:old)
    client.invalidateQueries({queryKey:['ai-conversations',owner]})
  },onError:error=>{
    if(error instanceof ApiError && error.status<500){
      pending.current=null
      if(error.status===409)client.invalidateQueries({queryKey:detailKey})
    }
  },onSettled:()=>{sending.current=false}})
  const cancel=useMutation({mutationFn:(id:string)=>api<Run>(`/ai/runs/${id}/cancel`,'POST',{}),onSuccess:run=>{
    client.setQueryData<Detail>(['ai-conversation',owner,run.conversation_id],old=>old?{...old,runs:old.runs.map(r=>r.id===run.id?run:r)}:old)
  }})
  function submit(event:FormEvent) {
    event.preventDefault()
    if(sending.current||busy||!prompt.trim())return
    sending.current=true;send.mutate()
  }
  const error=detail.error||conversations.error||catalog.error||create.error||send.error||cancel.error
  return <div className="chat-layout">
    <aside className="panel chat-history"><button className="primary full" disabled={create.isPending||send.isPending} onClick={()=>create.mutate()}>新建对话</button><h3>我的对话</h3>
      {conversations.isPending?<p className="muted">正在读取…</p>:null}
      {conversations.data?.map(row=><button className={'chat-history-item '+(row.id===selected?'selected':'')} key={row.id} disabled={send.isPending||create.isPending} onClick={()=>choose(row.id)}><strong>{row.title}</strong><small>{date(row.created_at)}</small></button>)}
      {!conversations.data?.length?<p className="muted">新建一段对话，开始提问。</p>:null}
    </aside>
    <section className="panel chat-main"><div className="panel-heading"><div><h3>{detail.data?.title||'和 AI 一起理解'}</h3><p className="muted">共享 DeepSeek · 历史仅自己可见 · 当前支持文字提问</p></div></div>
      {error?<div className="error" role="alert">{message(error)}</div>:null}
      {catalog.data&&!catalog.data.platform_enabled?<p className="error">共享模型服务尚未配置，请联系管理员。</p>:null}
      {selected&&detail.isPending?<p>正在打开对话…</p>:null}
      {!selected?<div className="empty">点击“新建对话”，无需填写个人 API Key。</div>:null}
      <div className="chat-messages" aria-label="对话记录">{detail.data?.runs.map(run=><article className="chat-turn" key={run.id}>
        <div className="chat-question"><small>你</small><p>{run.prompt}</p></div>
        <div className="chat-answer"><small>{run.model==='deepseek-flash'?'DeepSeek Flash':'DeepSeek V4 Pro'} · <span role="status">{labels[run.status]}</span></small>
          <div className="chat-answer-text">{run.answer||(active(run)?'正在准备回复…':'本次没有生成正文。')}</div>
          {run.error_code?<p className="chat-warning">{errors[run.error_code]||'本次回复未完成。'}</p>:null}
          {run.usage?.total_tokens!==undefined?<small>用量：{run.usage.total_tokens} tokens</small>:!active(run)&&run.status!=='succeeded'?<small>本次用量未完整返回；已产生的用量仍可能计费。</small>:null}
        </div></article>)}</div>
      {streamError?<div className="error" role="alert">{streamError}<button className="text-button" onClick={()=>{detail.refetch();setReadVersion(v=>v+1)}}>重新读取</button></div>:null}
      {selected?<form className="chat-composer" onSubmit={submit}><label>提问<textarea value={prompt} onChange={e=>setPrompt(e.target.value)} maxLength={8000} rows={4} placeholder="输入问题，例如：用生活中的例子解释矩阵乘法" disabled={busy||send.isPending||detail.isError}/></label>
        <div className="chat-controls"><label>模型<select aria-label="对话模型" value={model} onChange={e=>setModel(e.target.value)} disabled={busy||send.isPending}>{catalog.data?.providers.flatMap(p=>p.models).filter(m=>m.supports_text).map(m=><option value={m.id} key={m.id}>{m.name}</option>)}</select></label>
          {busy&&latest?<button type="button" className="secondary" disabled={cancel.isPending} onClick={()=>cancel.mutate(latest.id)}>停止生成</button>:<button className="primary" disabled={send.isPending||!prompt.trim()||!detail.data||!catalog.data?.platform_enabled}>{send.isPending?'正在提交…':'发送'}</button>}</div>
        <p className="muted chat-hint">每次最多 8000 字，每小时 30 次。刷新或切换页面后可查看原回复；取消会保留已保存的部分内容。AI 回答请自行核对。</p>
      </form>:null}
    </section>
  </div>
}
