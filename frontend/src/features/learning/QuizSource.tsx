import {useQuery} from '@tanstack/react-query'
import {api} from '../../api'
import {PrivateImage} from '../files/PrivateImage'
import {NoteError} from '../notes/NoteError'
import {sourceVisual,type SourceRead} from './cards'
export function QuizSource({owner,sessionId,path,onOpenNote}:{owner:string;sessionId:string;path:string;onOpenNote:(id:string)=>void}){
  const source=useQuery({queryKey:['quiz-source',owner,sessionId,path],queryFn:()=>api<SourceRead>(path),retry:false,gcTime:0,refetchOnMount:'always'})
  const read=source.isFetching||source.isError?null:source.data,visual=read?sourceVisual(read):null
  return <section className="card-source-read" aria-label="题目来源"><h4>只读来源</h4>{source.isFetching?<p role="status">正在重新核验来源…</p>:source.isError?<><p role="alert">来源已删除、失效或暂时不可读取。题目和作答仍保留，不读取旧图。</p><NoteError error={source.error}/></>:read?<><p>{read.source.title} · {read.source_changed?'历史快照来源':'来源'}版本 {read.source.revision}</p>{read.source.kind==='text'?<blockquote>{read.text||read.source.quote}</blockquote>:visual?<div className="card-source-image"><PrivateImage fileId={visual.fileId} kind="reading" alt={visual.title}/></div>:null}<button className="text-button" type="button" onClick={()=>onOpenNote(read.source.note_id)}>打开当前来源笔记</button></>:null}<button className="text-button" type="button" disabled={source.isFetching} onClick={()=>void source.refetch()}>重新核验来源</button></section>
}
