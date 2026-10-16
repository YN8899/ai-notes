import { useEffect, useRef, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api'
import type { components } from '../../api.generated'
import { ImageReader } from '../files/ImageReader'
import type { Run } from './chat-stream'
import { pageRange } from './pdf-pages'
import '../files/files.css'

export type PdfInput=components['schemas']['PdfInput']
export type PdfPreview=components['schemas']['PdfPreviewOut']
export type PdfBook=components['schemas']['PdfNotebookOut']
export type PdfDetail=components['schemas']['PdfNotebookDetail']
type SourcePage=components['schemas']['SourcePageOut']
type Ref=components['schemas']['PdfPageRef']
export type PdfChoice={noteId:string;mode:'pages'|'all';range:string}
export const emptyChoice:PdfChoice={noteId:'',mode:'pages',range:'1'}

export function PdfPicker({choice,onChange,books,book,preview,error,loading,disabled}:{choice:PdfChoice;onChange:(choice:PdfChoice)=>void;books:PdfBook[];book?:PdfDetail;preview?:PdfPreview;error:string;loading:boolean;disabled:boolean}) {
  return <fieldset className="pdf-picker" disabled={disabled}><legend>本次资料</legend>
    <label>PDF 笔记<select aria-label="PDF 笔记" value={choice.noteId} onChange={e=>onChange({...choice,noteId:e.target.value,range:'1'})}><option value="">仅文字，不附加 PDF</option>{books.map(book=><option key={book.id} value={book.id}>{book.title}（{book.page_count} 页）</option>)}</select></label>
    {!books.length?<p className="muted">需要提问 PDF 时，先到“我的笔记”导入并完成转换。</p>:null}
    {choice.noteId?<><div className="pdf-mode"><label><input type="radio" name="pdf-mode" checked={choice.mode==='pages'} onChange={()=>onChange({...choice,mode:'pages'})}/>指定页</label><label><input type="radio" name="pdf-mode" checked={choice.mode==='all'} onChange={()=>onChange({...choice,mode:'all'})}/>整本 PDF</label></div>
      {choice.mode==='pages'?<label>原 PDF 页码<input value={choice.range} maxLength={1000} onChange={e=>onChange({...choice,range:e.target.value})} placeholder="例如：2、4-6"/></label>:null}
      {loading?<p className="muted" role="status">正在核对页码与处理范围…</p>:null}
      {error?<p className="error" role="alert">{error}</p>:null}
      {preview&&!loading&&!error?<div className="pdf-preview" role="status"><strong>{choice.mode==='all'?'整本':'所选'} {preview.source.pages.length} 页 · 原 PDF 第 {pageRange(preview.source.pages.map(page=>page.number))} 页</strong><p>{preview.batches.length===1?'直接读取所选页图并回答':`分 ${preview.batches.length} 批读取，再综合回答`}，本次计划 {preview.planned_calls} 次模型调用。</p><small>仅发送上述页图与文字对话给 DeepSeek Flash。整本分析更慢，用量通常更多；中断后不会自动重新生成。页码以原 PDF 为准，更换 PDF 请新建对话。</small></div>:book&&!loading&&!error?<p className="muted">请选择有效页码。</p>:null}
    </>:null}
  </fieldset>
}

export function CitedAnswer({run,onOpen}:{run:Run;onOpen:(run:Run,page:Ref)=>void}) {
  const pieces:ReactNode[]=[]
  let offset=0
  for(const match of run.answer.matchAll(/\[\[p:(\d+)\]\]/g)) {
    const position=match.index!
    pieces.push(run.answer.slice(offset,position))
    const page=run.source?.pages.find(page=>page.number===Number(match[1]))
    pieces.push(page?<button type="button" className="citation-link" key={position} aria-label={'查看来源原 PDF 第'+page.number+'页'} onClick={()=>onOpen(run,page)}>第 {page.number} 页</button>:match[0])
    offset=position+match[0].length
  }
  pieces.push(run.answer.slice(offset))
  return <>{pieces}</>
}

export function PdfRunScope({run,onOpen}:{run:Run;onOpen:(run:Run,page:Ref)=>void}) {
  if(!run.source)return null
  const progress=run.progress
  const running=run.status==='queued'||run.status==='running'
  const stage=running&&progress?{queued:'等待读取',reading:'逐批读取中',reducing:'正在整理阅读记录',answering:'正在生成回答',done:'处理结束'}[progress.stage]:run.status==='succeeded'?'处理完成':run.status==='cancelled'?'已停止':'处理未完整完成'
  const currentPages=progress?.current_pages||[]
  return <div className="pdf-run-scope">
    <details><summary>本次资料：{run.source.title} · {run.source.mode==='all'?'整本 PDF':'指定页'} · 共 {run.source.pages.length} 页</summary>
      <p>原 PDF 第 {pageRange(run.source.pages.map(page=>page.number))} 页 · 来源版本 {run.source.revision}</p>
      <div className="pdf-page-links">{run.source.pages.map(page=><button type="button" className="text-button" key={page.page_id} onClick={()=>onOpen(run,page)}>查看原 PDF 第 {page.number} 页</button>)}</div>
    </details>
    {progress?<div className="pdf-run-progress" role="status"><strong>{stage} · 已完成读取 {progress.completed_pages} / {progress.total_pages} 页</strong><progress max={progress.total_pages} value={progress.completed_pages} aria-label="PDF 阅读进度"/><small>读取批次 {progress.completed_batches} / {progress.total_batches} · 已启动调用 {progress.call_count} / {progress.planned_calls}{currentPages.length?' · 当前阶段资料：原 PDF 第 '+pageRange(currentPages)+' 页':''}</small></div>:null}
  </div>
}

export function SourceReader({owner,run,page,onClose}:{owner:string;run:Run;page:Ref;onClose:()=>void}) {
  const container=useRef<HTMLElement>(null)
  const query=useQuery({queryKey:['ai-source',owner,run.id,page.page_id],queryFn:()=>api<SourcePage>(`/ai/runs/${run.id}/source-pages/${page.page_id}`),retry:false})
  useEffect(()=>{container.current?.scrollIntoView({block:'start',behavior:'smooth'})},[run.id,page.page_id,query.data])
  return <section className="pdf-source-reader" ref={container} aria-label="来源原页"><div className="panel-heading"><h4>来源原页 · 第 {page.number} 页</h4><button type="button" className="secondary" onClick={onClose}>返回对话</button></div>
    {query.isPending?<p>正在验证来源权限…</p>:query.error?<p className="error" role="alert">{query.error instanceof Error?query.error.message:'来源已不可用'}</p>:query.data?<>
      {query.data.source_changed?<p className="chat-warning">来源笔记已更新。此引用对应版本 {query.data.source_revision} 的原 PDF 页图，页码不随笔记排序改变。</p>:null}
      <ImageReader key={query.data.file_id} fileId={query.data.file_id} filename={query.data.title+' · 原 PDF 第'+query.data.page_number+'页'}/>
    </>:null}
  </section>
}
