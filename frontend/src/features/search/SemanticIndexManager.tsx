import {useRef,useState,useSyncExternalStore} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {api,date} from '../../api'
import {authSession} from '../../session.ts'
import {createUuid} from '../../uuid.ts'
import {NoteError} from '../notes/NoteError'
import {semanticAmbiguous,semanticLabels,semanticReason,semanticUsage,type SemanticStatus,type SemanticPreview,type SemanticIndex,type SemanticBuild} from './semantic'
import './semantic.css'

export function SemanticIndexManager({owner}:{owner:string}) {
  const session=useSyncExternalStore(authSession.subscribe,authSession.snapshot)
  if(session.phase!=='active'||session.owner!==owner)return null
  return <Manager key={owner+':'+session.id} owner={owner} sessionId={session.id}/>
}
function Manager({owner,sessionId}:{owner:string;sessionId:string}) {
  const client=useQueryClient(),[ticket]=useState(()=>authSession.ticket()),pollUntil=useRef(Date.now()+300000)
  const valid=()=>authSession.current(ticket,owner)&&ticket.id===sessionId
  const key=['semantic',owner,sessionId]
  const status=useQuery({queryKey:[...key,'status'],queryFn:()=>api<SemanticStatus>('/semantic/status'),retry:false})
  const indexes=useQuery({queryKey:[...key,'indexes'],queryFn:()=>api<SemanticIndex[]>('/semantic/indexes'),retry:false,refetchInterval:query=>Date.now()<pollUntil.current&&query.state.data?.some(item=>['queued','building'].includes(item.status))?2000:false})
  const [preview,setPreview]=useState<SemanticPreview|null>(null),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[attempt,setAttempt]=useState<SemanticBuild|null>(null),[uncertain,setUncertain]=useState(false),[notice,setNotice]=useState('')
  const pending=useRef(false)
  async function refresh(){pollUntil.current=Date.now()+300000;await Promise.all([status.refetch(),indexes.refetch()])}
  async function prepare(){if(pending.current)return;pending.current=true;setBusy(true);setError(null);setPreview(null);setConfirmed(false);try{const value=await api<SemanticPreview>('/semantic/preview');if(valid())setPreview(value)}catch(e){if(valid())setError(e)}finally{pending.current=false;if(valid())setBusy(false)}}
  async function submit(payload:SemanticBuild){if(pending.current)return;pending.current=true;setBusy(true);setError(null);setNotice('');try{await api<SemanticIndex>('/semantic/indexes','POST',payload);if(valid()){setUncertain(false);setAttempt(null);setPreview(null);setConfirmed(false);setNotice('构建请求已登记。后台构建不会自动切换当前索引。');await refresh()}}catch(e){if(valid()){setError(e);setUncertain(semanticAmbiguous(e));if(!semanticAmbiguous(e))setAttempt(null)}}finally{pending.current=false;if(valid())setBusy(false)}}
  async function build(){if(!preview||!confirmed||pending.current)return;const payload:SemanticBuild={request_key:createUuid(),manifest_hash:preview.manifest_hash,confirm_external:true};setAttempt(payload);await submit(payload)}
  async function reconcile(){if(!attempt||pending.current)return;pending.current=true;setBusy(true);setError(null);try{const data=await api<SemanticIndex[]>('/semantic/indexes');if(valid()){client.setQueryData([...key,'indexes'],data);if(data.some(item=>item.request_key===attempt.request_key)){setUncertain(false);setAttempt(null);setPreview(null);setConfirmed(false);setNotice('已找到原请求，请查看下方进度。')}else setNotice('暂未找到原请求。可稍后再次核对，或明确重发同一请求；不会更换请求编号。')}}catch(e){if(valid())setError(e)}finally{pending.current=false;if(valid())setBusy(false)}}
  async function activate(index:SemanticIndex){if(pending.current)return;pending.current=true;setBusy(true);setError(null);try{await api(`/semantic/indexes/${encodeURIComponent(index.id)}/activate`,'POST',{});if(valid()){setNotice('已切换激活索引。');await refresh()}}catch(e){if(valid())setError(e)}finally{pending.current=false;if(valid())setBusy(false)}}
  if(!valid())return null
  const available=status.data?.enabled&&status.data.configured&&status.data.available
  const active=indexes.data?.find(item=>item.status==='active')
  return <section className="panel semantic-panel" aria-label="语义索引管理">
    <div className="panel-heading"><h3>语义检索索引</h3><button type="button" className="text-button" disabled={busy||indexes.isFetching} onClick={()=>void refresh()}>刷新索引状态</button></div>
    <p className="muted">自管向量索引 · 百炼文本 embedding（向量化）。仅用于语义检索，暂不生成问答。</p>
    {status.isPending?<p role="status">正在读取语义服务状态…</p>:status.data?<p role="status">{semanticReason(status.data)}</p>:null}
    <NoteError error={error||status.error||indexes.error}/>
    <p className="muted">预览和状态刷新不调用外部模型。构建会将当前账号的笔记文本、标签及有效 OCR 文字发送至百炼；不发送图片、PDF 或其他原文件。每次最多 500 个分块，超限需缩小资料量。</p>
    <button type="button" className="secondary" disabled={!available||busy||uncertain||!!status.data?.building_index_id} onClick={()=>void prepare()}>预览本次构建</button>
    {preview?<div className="semantic-preview" aria-label="构建预览"><h4>本次构建预览</h4><p>{preview.config.model} · {preview.config.dimensions} 维 · {preview.config.provider} / {preview.config.workspace}</p><div className="semantic-facts"><span>{preview.source_count} 个来源</span><span>{preview.chunk_count} / {Math.min(preview.max_chunks,500)} 个分块</span><span>{preview.character_count} 字符</span><span>{preview.batch_count} 批外部请求</span></div><p className="muted">来源分布：{Object.entries(preview.sources).map(([name,count])=>`${name}: ${count}`).join(' · ')||'无可索引来源'}</p><label className="semantic-confirm"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我确认将以上文字发送至百炼构建索引，可能产生服务商费用。</label><button type="button" className="primary" disabled={!confirmed||busy||uncertain||!preview.chunk_count||preview.chunk_count>Math.min(preview.max_chunks,500)} onClick={()=>void build()}>{busy?'正在登记…':'确认并构建索引'}</button></div>:null}
    {uncertain&&attempt?<div className="semantic-warning" role="alert"><p>网络响应不确定，原请求可能已登记。请先核对原请求；页面不会自动重试，也不会生成新请求编号。</p><p className="semantic-key">请求编号：{attempt.request_key}</p><div className="note-actions"><button type="button" className="secondary" disabled={busy} onClick={()=>void reconcile()}>核对原请求</button><button type="button" className="text-button" disabled={busy} onClick={()=>void submit(attempt)}>明确重发同一请求</button></div><p className="muted">重发沿用相同编号和预览；服务端去重。若原请求未到达，才会登记构建。</p></div>:null}
    {notice?<p role="status">{notice}</p>:null}
    <p className="muted">构建完成后请手动激活。切换或回退已有就绪索引不调用百炼、不产生模型费用。笔记或 OCR 更新后，请重新预览并构建；过期来源会拒绝展示。自动状态轮询最多 5 分钟，可手动刷新继续查看。</p>
    <ul className="semantic-index-list">{indexes.data?.map(index=><li key={index.id}><div className="panel-heading"><h4>{semanticLabels[index.status]}</h4><span className="muted">{date(index.created_at)}</span></div><p>{index.config.model} · {index.config.dimensions} 维 · {index.source_count} 个来源</p><progress aria-label="索引构建进度" value={index.completed_chunks} max={Math.max(index.chunk_count,1)}/><p className="muted">{index.completed_chunks} / {index.chunk_count} 分块 · {semanticUsage(index.usage)}</p>{index.error_code?<p className="error">构建失败：{index.error_code}。请检查服务配置后重新预览；不会自动重试。</p>:null}{index.status==='ready'?<button type="button" className="secondary" disabled={busy} onClick={()=>void activate(index)}>{active&&index.created_at<active.created_at?'回退到此索引（无模型费用）':'激活此索引（无模型费用）'}</button>:null}</li>)}</ul>
    {indexes.data?.length===0?<p className="empty">尚未创建语义索引。</p>:null}
  </section>
}
