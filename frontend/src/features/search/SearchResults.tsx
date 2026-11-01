import {useEffect,useRef,useState,useSyncExternalStore} from 'react'
import {useQuery} from '@tanstack/react-query'
import {api,ApiError,date} from '../../api'
import type {components} from '../../api.generated'
import {authSession} from '../../session.ts'
import {describeRegion} from '../ai/image-region'
import {ImageReader} from '../files/ImageReader'
import {NoteError} from '../notes/NoteError'
import {highlightedParts,nextSearchPage,searchParams,searchScope,type SearchFilters} from './keyword'
import '../files/files.css'
import './keyword.css'

type SearchHit=components['schemas']['SearchHitOut']
type SearchPage=components['schemas']['SearchPageOut']
type SearchSource=components['schemas']['SearchSourceOut']
type Props={owner:string;filters:SearchFilters;onOpenNote:(id:string)=>void}
const fields:Record<string,string>={title:'标题',tag:'标签',body:'正文',ocr:'OCR 识别文字'}

export function SearchResults(props:Props) {
  const session=useSyncExternalStore(authSession.subscribe,authSession.snapshot)
  if(session.phase!=='active'||session.owner!==props.owner)return null
  return <ScopedResults key={searchScope(props.owner,session.id,props.filters)} {...props} sessionId={session.id}/>
}

function Highlight({text,start,end}:{text:string;start:number;end:number}) {
  const [before,match,after]=highlightedParts(text,start,end)
  return <>{before}{match?<mark>{match}</mark>:null}{after}</>
}

function ScopedResults({owner,filters,onOpenNote,sessionId}:Props&{sessionId:string}) {
  const [cursors,setCursors]=useState<string[]>(['']),[selected,setSelected]=useState<SearchHit|null>(null),[attempt,setAttempt]=useState(0)
  const [ticket]=useState(()=>authSession.ticket())
  const cursor=cursors[cursors.length-1],params=searchParams(filters,cursor)
  const valid=authSession.current(ticket,owner)&&ticket.id===sessionId
  const invalid=Array.from(filters.q.trim()).length>100
  const results=useQuery({queryKey:['keyword',owner,sessionId,params.toString()],queryFn:()=>api<SearchPage>('/search?'+params),enabled:valid&&!invalid,retry:false})
  function firstPage(){setSelected(null);setCursors(['']);if(!cursor)void results.refetch()}
  if(!valid)return null
  return <section className="keyword-results" aria-label="关键词搜索结果">
    <div className="panel-heading"><h3>关键词搜索结果</h3><button type="button" className="text-button" disabled={results.isFetching||invalid} onClick={firstPage}>重新搜索</button></div>
    <p className="muted">搜索当前账号未删除笔记的标题、标签、正文和有效 OCR 文字。连续字串匹配，搜索不会调用模型。</p>
    {invalid?<p className="error" role="alert">关键词最多 100 个字符，请缩短后重试。</p>:results.isPending?<p role="status">正在搜索…</p>:results.isError?<><NoteError error={results.error}/><button type="button" className="secondary" onClick={()=>void results.refetch()}>重试搜索</button></>:results.data?.items.length?<ul className="keyword-list">{results.data.items.map(hit=><li className="keyword-hit" key={hit.kind+':'+hit.id}>
      <div className="keyword-meta"><span className="badge">{hit.kind==='ocr'?'OCR':'笔记'}</span><span>{fields[hit.matched_field]}</span>{hit.kind==='ocr'?<span>第 {hit.page_number} 页 · {hit.region?'矩形选区':'整页'} · {hit.reviewed?'已复核':'未复核'}</span>:null}</div>
      <h4>{hit.title}</h4><p className="keyword-snippet"><Highlight text={hit.snippet} start={hit.match_start} end={hit.match_end}/></p>
      <div className="keyword-meta"><span>{hit.note_kind==='image'?'图片笔记':'文字笔记'} · {date(hit.updated_at)}</span><button type="button" className="secondary" onClick={()=>{setSelected(hit);setAttempt(value=>value+1)}}>查看匹配来源</button></div>
    </li>)}</ul>:<p className="empty" role="status">没有找到匹配内容。请调整关键词或筛选条件。</p>}
    {!invalid?<div className="note-pagination"><span className="muted">第 {cursors.length} 页 · 当前可访问结果</span><div className="note-actions"><button type="button" className="secondary" disabled={cursors.length===1||results.isFetching} onClick={()=>{setSelected(null);setCursors(old=>old.slice(0,-1))}}>搜索上一页</button><button type="button" className="secondary" disabled={results.isError||!results.data?.next_cursor||results.isFetching} onClick={()=>{setSelected(null);setCursors(old=>nextSearchPage(old,results.data?.next_cursor))}}>搜索下一页</button></div></div>:null}
    {selected?<SourceReader key={selected.kind+':'+selected.id+':'+attempt} owner={owner} sessionId={sessionId} q={filters.q.trim()} hit={selected} onClose={()=>setSelected(null)} onOpenNote={onOpenNote} onSearchAgain={firstPage}/>:null}
  </section>
}

function SourceReader({owner,sessionId,q,hit,onClose,onOpenNote,onSearchAgain}:{owner:string;sessionId:string;q:string;hit:SearchHit;onClose:()=>void;onOpenNote:(id:string)=>void;onSearchAgain:()=>void}) {
  const params=new URLSearchParams({q,source_revision:String(hit.source_revision),match_hash:hit.match_hash})
  if(hit.ocr_revision!==null&&hit.ocr_revision!==undefined)params.set('ocr_revision',String(hit.ocr_revision))
  const source=useQuery({queryKey:['keyword-source',owner,sessionId,hit.kind,hit.id,params.toString()],queryFn:()=>api<SearchSource>(`/search/${hit.kind}/${encodeURIComponent(hit.id)}/source?${params}`),retry:false,gcTime:0,refetchOnMount:'always'})
  const unavailable=source.error instanceof ApiError&&[404,409].includes(source.error.status)
  return <section className="keyword-source" aria-label="匹配来源只读阅读">
    <div className="panel-heading"><h4>匹配来源 · 只读</h4><button type="button" className="text-button" onClick={onClose}>关闭来源</button></div>
    {source.isPending||source.isFetching?<p role="status">正在重新核验来源…</p>:source.isError?unavailable?<div role="alert"><p className="error">{source.error instanceof ApiError&&source.error.status===409?'来源内容或识别版本已变化，请重新搜索。':'来源已删除、失效或无法访问，请重新搜索。'}</p><button type="button" className="secondary" onClick={onSearchAgain}>重新搜索并核对来源</button></div>:<><NoteError error={source.error}/><button type="button" className="secondary" onClick={()=>void source.refetch()}>重试读取来源</button></>:source.data?<SourceContent source={source.data} onOpenNote={onOpenNote}/>:null}
  </section>
}

export function SourceContent({source,onOpenNote}:{source:SearchSource;onOpenNote:(id:string)=>void}) {
  const text=useRef<HTMLPreElement>(null)
  useEffect(()=>{
    const host=text.current,mark=host?.querySelector('mark')
    if(host&&mark)host.scrollTop=mark.getBoundingClientRect().top-host.getBoundingClientRect().top+host.scrollTop-host.clientHeight/2
  },[source])
  return <><h4>{source.title}</h4><p className="muted">匹配字段：{fields[source.matched_field]}{source.kind==='ocr'?` · 第 ${source.page_number} 页 · ${source.reviewed?'已复核':'未复核'}`:''}</p>
    <pre ref={text} tabIndex={0} className="keyword-source-text" aria-label="匹配原文"><Highlight text={source.text} start={source.match_start} end={source.match_end}/></pre>
    {source.kind==='ocr'&&source.file_id?<><p className="muted">{source.region?describeRegion(source.region)+'。绿色边框标出识别时的矩形选区。':'来源为整页识别。'}</p><ImageReader key={source.file_id} fileId={source.file_id} filename={source.title+' · 第 '+source.page_number+' 页'} region={source.region}/></>:null}
    <div className="note-actions"><button type="button" className="secondary" onClick={()=>onOpenNote(source.note_id)}>打开当前原笔记</button><span className="muted">原文只读；打开笔记后可按原有流程编辑。</span></div>
  </>
}
