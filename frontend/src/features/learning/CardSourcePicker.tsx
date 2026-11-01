import {useEffect,useMemo,useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {api} from '../../api'
import type {components} from '../../api.generated'
import {canonicalText} from '../notes/text-selection'
import {NoteError} from '../notes/NoteError'
import {RegionCanvas} from '../ai/RegionSelection'
import {textRange,type Selection,type Visual} from './cards'
import type {Region} from '../ai/image-region'
import '../files/files.css'
import '../ai/ai.css'

type Props={owner:string;sessionId:string;onChange:(source:Selection|null,visual:Visual|null)=>void;disabled?:boolean;required?:boolean}
type Book=components['schemas']['ImageNotebookOut']
type Detail=components['schemas']['ImageNotebookDetail']|components['schemas']['PdfNotebookDetail']
export function CardSourcePicker({owner,sessionId,onChange,disabled=false,required=false}:Props){
  const [kind,setKind]=useState<'none'|'text'|'image'|'pdf'>('none'),[noteId,setNoteId]=useState(''),[pageId,setPageId]=useState(''),[region,setRegion]=useState<Region|null>(null),[cropping,setCropping]=useState(false),[ready,setReady]=useState('')
  const [cursor,setCursor]=useState(''),[previous,setPrevious]=useState<string[]>([]),[start,setStart]=useState(0),[end,setEnd]=useState(0)
  const notes=useQuery({queryKey:['card-sources',owner,sessionId,'text',cursor],queryFn:()=>api<components['schemas']['NoteList']>('/notes?kind=text&limit=50'+(cursor?'&cursor='+encodeURIComponent(cursor):'')),enabled:kind==='text',retry:false})
  const books=useQuery({queryKey:['card-sources',owner,sessionId,kind],queryFn:()=>api<Book[]>('/ai/'+kind+'-notebooks'),enabled:kind==='image'||kind==='pdf',retry:false})
  const detail=useQuery({queryKey:['card-source-detail',owner,sessionId,kind,noteId],queryFn:()=>api<Detail|components['schemas']['NoteOut']>(kind==='text'?'/notes/'+noteId:'/ai/'+kind+'-notebooks/'+noteId),enabled:kind!=='none'&&!!noteId,retry:false,gcTime:0})
  const text=detail.data&&'body_json'in detail.data?canonicalText(detail.data.body_json):''
  const length=Array.from(text).length
  useEffect(()=>{setStart(0);setEnd(Math.min(length,8000))},[noteId,length,detail.data?.revision])
  const page=detail.data&&'pages'in detail.data?detail.data.pages.find(page=>page.page_id===pageId):undefined
  const scope=useMemo(()=>{
    if(kind==='none'||!noteId||!detail.data||detail.isFetching||detail.isError||detail.data.id!==noteId)return {source:null,visual:null,error:''}
    try{
      if(kind==='text')return {source:{kind:'text' as const,note_id:noteId,revision:detail.data.revision,...textRange(text,start,end)},visual:null,error:''}
      if(!page||cropping&&!region)return {source:null,visual:null,error:''}
      const source:Selection=kind==='image'?{kind:'image',note_id:noteId,revision:detail.data.revision,page_id:page.page_id,region:cropping?region:null}:{kind:'pdf',note_id:noteId,revision:detail.data.revision,mode:cropping?'region':'pages',page_numbers:[page.number],region:cropping?region:null}
      return {source,visual:{fileId:page.file_id,title:detail.data.title,region:cropping?region:null},error:''}
    }catch(e){return {source:null,visual:null,error:e instanceof Error?e.message:'来源无效'}}
  },[kind,noteId,detail.data,detail.isFetching,detail.isError,text,start,end,page,cropping,region])
  // This only reports local selection; no preview or model request is made here.
  useEffect(()=>{onChange(scope.source,scope.visual)},[scope,onChange])
  function chooseKind(next:typeof kind){setKind(next);setNoteId('');setPageId('');setRegion(null);setReady('');setCursor('');setPrevious([])}
  return <fieldset className="card-source-picker" disabled={disabled}><legend>资料来源{required?'（必选）':'（可选）'}</legend>
    <label>来源类型<select aria-label="来源类型" value={kind} onChange={e=>chooseKind(e.target.value as typeof kind)}><option value="none">暂不关联来源</option><option value="text">文字笔记选段</option><option value="image">图片笔记单页 / 选区</option><option value="pdf">PDF 单页 / 选区</option></select></label>
    {kind!=='none'?<><label>来源笔记<select aria-label="来源笔记" value={noteId} onChange={e=>{setNoteId(e.target.value);setPageId('');setRegion(null);setReady('')}}><option value="">选择当前账号的笔记</option>{(kind==='text'?notes.data?.items:books.data)?.map(note=><option key={note.id} value={note.id}>{note.title}</option>)}</select></label>
      <NoteError error={notes.error||books.error||detail.error}/>{detail.isFetching?<p role="status">正在核对来源版本…</p>:null}
      {kind==='text'?<><div className="card-actions"><button type="button" className="text-button" disabled={!previous.length} onClick={()=>{setCursor(previous.at(-1)||'');setPrevious(old=>old.slice(0,-1));setNoteId('')}}>上一页来源笔记</button><button type="button" className="text-button" disabled={!notes.data?.next_cursor} onClick={()=>{setPrevious(old=>[...old,cursor]);setCursor(notes.data!.next_cursor!);setNoteId('')}}>下一页来源笔记</button></div>{noteId&&text?<><label>只读原文（可选中一段后点击“使用选中文字”）<textarea aria-label="来源只读原文" readOnly value={text} rows={5} onSelect={e=>{const input=e.currentTarget;if(input.selectionEnd>input.selectionStart){input.dataset.start=String(Array.from(text.slice(0,input.selectionStart)).length);input.dataset.end=String(Array.from(text.slice(0,input.selectionEnd)).length)}}} id={'card-source-text-'+sessionId}/></label><div className="card-actions"><button type="button" className="secondary" onClick={()=>{const input=document.getElementById('card-source-text-'+sessionId) as HTMLTextAreaElement;setStart(Number(input.dataset.start||0));setEnd(Number(input.dataset.end||0))}}>使用选中文字</button><button type="button" className="text-button" onClick={()=>{setStart(0);setEnd(Math.min(length,8000))}}>{length<=8000?'使用整篇笔记':'使用前 8000 字符'}</button></div><div className="card-range"><label>开始字符（从 0 起）<input type="number" min={0} max={Math.max(0,length-1)} value={start} onChange={e=>setStart(Number(e.target.value))}/></label><label>结束字符（不含）<input type="number" min={1} max={length} value={end} onChange={e=>setEnd(Number(e.target.value))}/></label></div><p className="muted">共 {length} 个字符 · 使用已保存版本 {detail.data?.revision}</p>{scope.source?.kind==='text'?<blockquote className="card-quote">{scope.source.quote}</blockquote>:null}</>:noteId&&!detail.isFetching?<p className="muted">这篇笔记暂无可用文字。</p>:null}</>:null}
      {kind!=='text'&&detail.data&&'pages'in detail.data&&!detail.isFetching?<><label>来源页面<select aria-label="来源页面" value={pageId} onChange={e=>{setPageId(e.target.value);setRegion(null);setReady('')}}><option value="">请选择一页</option>{detail.data.pages.map(page=><option key={page.page_id} value={page.page_id}>{kind==='pdf'?'原 PDF ':''}第 {page.number} 页</option>)}</select></label><label className="card-check"><input type="checkbox" checked={cropping} onChange={e=>{setCropping(e.target.checked);setRegion(null)}}/>仅使用页面中的矩形选区</label>{page?<RegionCanvas key={kind+page.file_id} fileId={page.file_id} title={detail.data.title} region={region} selecting={cropping} disabled={disabled} onChange={setRegion} onReady={setReady}/>:null}{cropping&&!ready?<p className="muted">等待图片加载后可画框。</p>:null}</>:null}
      {scope.error?<p className="error" role="alert">{scope.error}</p>:null}
    </>:null}
  </fieldset>
}
