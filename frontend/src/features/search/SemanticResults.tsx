import {useRef,useState,useSyncExternalStore} from 'react'
import {useQuery} from '@tanstack/react-query'
import {api,ApiError} from '../../api'
import type {components} from '../../api.generated'
import {authSession} from '../../session.ts'
import {createUuid} from '../../uuid.ts'
import {NoteError} from '../notes/NoteError'
import {SourceContent} from './SearchResults'
import {searchScope,type SearchFilters} from './keyword'
import {semanticAmbiguous,semanticRequest,semanticReason,semanticUsage,type SemanticStatus,type SemanticQuery,type SemanticRequest} from './semantic'
import './semantic.css'

type Props={owner:string;filters:SearchFilters;onOpenNote:(id:string)=>void;onKeyword:()=>void}
export function SemanticResults(props:Props) {
  const session=useSyncExternalStore(authSession.subscribe,authSession.snapshot)
  if(session.phase!=='active'||session.owner!==props.owner)return null
  return <Results key={searchScope(props.owner,session.id,props.filters)} {...props} sessionId={session.id}/>
}
function Results({owner,sessionId,filters,onOpenNote,onKeyword}:Props&{sessionId:string}) {
  const [ticket]=useState(()=>authSession.ticket()),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[result,setResult]=useState<SemanticQuery|null>(null),[attempt,setAttempt]=useState<SemanticRequest|null>(null),[uncertain,setUncertain]=useState(false),[selected,setSelected]=useState(''),[readAttempt,setReadAttempt]=useState(0)
  const pending=useRef(false),pollUntil=useRef(0)
  const valid=()=>authSession.current(ticket,owner)&&ticket.id===sessionId
  const status=useQuery({queryKey:['semantic',owner,sessionId,'status'],queryFn:()=>api<SemanticStatus>('/semantic/status'),retry:false})
  const tracking=useQuery({queryKey:['semantic-query',owner,sessionId,result?.id],queryFn:()=>api<SemanticQuery>('/semantic/queries/'+encodeURIComponent(result!.id)),enabled:!!result?.id,retry:false,refetchOnWindowFocus:false,refetchInterval:query=>Date.now()<pollUntil.current&&(query.state.data||result)?.status==='running'?2000:false})
  const current=tracking.data||result
  async function submit(payload:SemanticRequest){if(pending.current)return;pending.current=true;setBusy(true);setError(null);setSelected('');try{const data=await api<SemanticQuery>('/semantic/queries','POST',payload);if(valid()){setResult(data);setUncertain(false);setConfirmed(false);pollUntil.current=Date.now()+300000}}catch(e){if(valid()){setError(e);setUncertain(semanticAmbiguous(e));setConfirmed(false)}}finally{pending.current=false;if(valid())setBusy(false)}}
  function search(){if(!confirmed||pending.current)return;try{const payload=semanticRequest(filters,createUuid());setAttempt(payload);setResult(null);void submit(payload)}catch(e){setError(e)}}
  if(!valid())return null
  const length=Array.from(filters.q.trim()).length,invalid=length<1||length>100
  const canSearch=status.data?.enabled&&status.data.configured&&status.data.available&&!!status.data.active_index_id
  return <section className="keyword-results semantic-panel" aria-label="语义搜索结果"><div className="panel-heading"><h3>语义搜索</h3><button type="button" className="text-button" onClick={onKeyword}>切回关键词搜索</button></div>
    <p className="muted">按含义查找当前账号的笔记和有效 OCR 文字，最多返回 20 条来源。此功能暂不生成问答。修改输入或筛选会清空结果；不会自动发送查询。</p>
    {status.isPending?<p role="status">正在读取语义服务状态…</p>:status.data?<p role="status">{semanticReason(status.data)}</p>:null}
    <NoteError error={error||status.error||tracking.error}/>
    {invalid?<p className="muted">请输入 1–100 个字符的查询，再确认发送。</p>:null}
    <label className="semantic-confirm"><input type="checkbox" checked={confirmed} disabled={busy||!canSearch||invalid} onChange={e=>setConfirmed(e.target.checked)}/>我确认将本次查询文字发送至百炼进行向量化，可能产生服务商费用。</label>
    <button type="button" className="primary" disabled={!confirmed||busy||invalid||!canSearch||uncertain||current?.status==='running'} onClick={search}>{busy?'正在发送查询…':'确认并进行语义搜索'}</button>
    {uncertain&&attempt?<div className="semantic-warning" role="alert"><p>网络响应不确定，原查询可能已执行并计费。不会自动重试。下方只重发同一请求编号；若原请求未到达，重发会执行一次付费查询。</p><p className="semantic-key">请求编号：{attempt.request_key}</p><button type="button" className="secondary" disabled={busy} onClick={()=>void submit(attempt)}>核对 / 重发原查询（可能计费）</button><p className="muted">更改查询、筛选或离开页面后再次发起属于新查询，可能再次计费。</p></div>:null}
    {current?<><p className="muted" role="status">{current.status==='running'?'正在检索，仅读取状态…':current.status==='failed'?'查询失败':'查询完成'} · {semanticUsage(current.usage)}</p>{current.status==='running'||tracking.error?<button type="button" className="secondary" disabled={tracking.isFetching} onClick={()=>{pollUntil.current=Date.now()+300000;void tracking.refetch()}}>核对查询状态（不调用模型）</button>:null}{current.error_code?<p className="error">{current.error_code}。请检查服务配置或重新构建过期索引，不会自动重试。</p>:null}{current.status==='succeeded'?current.items.length?<ul className="keyword-list">{current.items.map(hit=><li className="keyword-hit" key={hit.chunk_id}><div className="keyword-meta"><span className="badge">{hit.kind==='ocr'?'OCR':'笔记'}</span>{hit.kind==='ocr'?<span>第 {hit.page_number} 页 · {hit.reviewed?'已复核':'未复核'}</span>:null}<span>相似度 {hit.score.toFixed(3)}</span></div><h4>{hit.title}</h4><p className="keyword-snippet">{hit.snippet}</p><button type="button" className="secondary" onClick={()=>{setSelected(hit.chunk_id);setReadAttempt(value=>value+1)}}>查看语义来源</button></li>)}</ul>:<p className="empty" role="status">没有找到当前可访问的匹配来源。请调整查询，或更新索引。</p>:null}</>:null}
    {selected?<SemanticSource key={selected+':'+readAttempt} owner={owner} sessionId={sessionId} chunkId={selected} onClose={()=>setSelected('')} onOpenNote={onOpenNote}/>:null}
  </section>
}
function SemanticSource({owner,sessionId,chunkId,onClose,onOpenNote}:{owner:string;sessionId:string;chunkId:string;onClose:()=>void;onOpenNote:(id:string)=>void}) {
  const source=useQuery({queryKey:['semantic-source',owner,sessionId,chunkId],queryFn:()=>api<components['schemas']['SearchSourceOut']>('/semantic/chunks/'+encodeURIComponent(chunkId)+'/source'),retry:false,gcTime:0,refetchOnMount:'always'})
  const stale=source.error instanceof ApiError&&[404,409].includes(source.error.status)
  return <section className="keyword-source" aria-label="语义来源只读阅读"><div className="panel-heading"><h4>语义来源 · 只读</h4><button type="button" className="text-button" onClick={onClose}>关闭来源</button></div>{source.isPending||source.isFetching?<p role="status">正在重新核验来源…</p>:source.isError?stale?<p className="error" role="alert">来源已删除、发生变化或 OCR 版本已更新，无法展示旧内容。请在模型设置中重新构建并激活索引。</p>:<><NoteError error={source.error}/><button type="button" className="secondary" onClick={()=>void source.refetch()}>重试读取来源（不调用模型）</button></>:source.data?<SourceContent source={source.data} onOpenNote={onOpenNote}/>:null}</section>
}
