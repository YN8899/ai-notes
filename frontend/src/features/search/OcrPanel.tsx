import {ModelPicker} from '../ai/ModelPicker'
import {defaultModel,modelReady,modelLabel,type ModelChoice} from '../ai/model-catalog'
import {lazy,Suspense,useEffect,useMemo,useRef,useState,useSyncExternalStore} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {api,ApiError,date,type AiCatalog} from '../../api'
import type {components} from '../../api.generated'
import {authSession} from '../../session.ts'
import {createUuid} from '../../uuid'
import {RegionCanvas} from '../ai/RegionSelection'
import {describeRegion,type Region} from '../ai/image-region'
import {ImageReader} from '../files/ImageReader'
import type {PageOut} from '../files/types'
import {NoteError} from '../notes/NoteError'
import {draftFrom,isOcrActive,reconcileDraft} from './ocr-draft'
import {ocrPreviewBody} from './ocr-preview'
import '../ai/ai.css'
import './ocr.css'

type OcrDocument=components['schemas']['OcrDocumentOut']&{provider?:string}
type Props={noteId:string;ownerId:string;revision:number;page:PageOut;canMutate:boolean;beforeMutation:()=>boolean;onBusyChange:(busy:boolean)=>void}
const pageSize=20
const FormattedAnswer=lazy(()=>import('../ai/FormattedAnswer').then(module=>({default:module.FormattedAnswer})))
const statuses:Record<string,string>={queued:'等待识别',running:'识别中',succeeded:'识别完成',failed:'识别失败',truncated:'输出被截断，未完成识别',interrupted:'识别中断',cancelled:'识别已取消'}

export function OcrPanel({noteId,ownerId,revision,page,canMutate,beforeMutation,onBusyChange}:Props) {
  const session=useSyncExternalStore(authSession.subscribe,authSession.snapshot)
  // The parent keys this panel by owner/note/page/file; a session replacement also
  // hides its contents immediately, before any late response can become visible.
  const [ticket]=useState(()=>authSession.ticket()),alive=useRef(true),posting=useRef(false),callbacks=useRef({onBusyChange})
  callbacks.current={onBusyChange}
  const authorized=()=>alive.current&&authSession.current(ticket,ownerId)
  const valid=authSession.current(ticket,ownerId)
  const [mode,setMode]=useState<'page'|'region'>('page'),[region,setRegion]=useState<Region|null>(null),[ready,setReady]=useState('')
  const [confirmation,setConfirmation]=useState<{revision:number;region:Region|null}|null>(null),[creating,setCreating]=useState(false),[error,setError]=useState<unknown>(null),[notice,setNotice]=useState('')
  const [choice,setChoice]=useState<ModelChoice>(defaultModel)
  useEffect(()=>setConfirmation(null),[choice])
  const [selected,setSelected]=useState(''),[offset,setOffset]=useState(0)
  const client=useQueryClient(),scope=['ocr',ownerId,ticket.id,noteId,page.id,page.file.id] as const
  const listKey=[...scope,'list',offset] as const
  const catalog=useQuery({queryKey:['ai',ownerId,'providers'],queryFn:()=>api<AiCatalog>('/ai/providers'),enabled:valid,retry:false})
  const attempts=useQuery({queryKey:listKey,queryFn:()=>api<OcrDocument[]>(`/ocr/documents?note_id=${encodeURIComponent(noteId)}&page_id=${encodeURIComponent(page.id)}&offset=${offset}&limit=${pageSize}`),enabled:valid,retry:false,
    refetchInterval:query=>query.state.data?.some(document=>isOcrActive(document.status))?2000:false})
  const detailKey=[...scope,'document',selected] as const
  const detail=useQuery({queryKey:detailKey,queryFn:()=>api<OcrDocument>('/ocr/documents/'+selected),enabled:valid&&!!selected,retry:false,
    refetchInterval:query=>query.state.data&&isOcrActive(query.state.data.status)?2000:false})
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;if(posting.current)callbacks.current.onBusyChange(false)}},[])
  useEffect(()=>{if(!valid){setConfirmation(null);setSelected('');setRegion(null);setError(null);setNotice('')}},[valid,session.generation])
  const previousRevision=useRef(revision)
  useEffect(()=>{if(previousRevision.current!==revision){previousRevision.current=revision;setConfirmation(null);void client.invalidateQueries({queryKey:['ocr',ownerId,ticket.id,noteId,page.id,page.file.id]})}},[revision,client,ownerId,ticket.id,noteId,page.id,page.file.id])
  const configured=modelReady(catalog.data,choice,{vision:true})
  const canCreate=valid&&canMutate&&configured&&!creating&&(mode==='page'||ready===page.file.id&&!!region)

  async function createAttempt() {
    if(posting.current||!confirmation||!canCreate||!authorized())return
    if(confirmation.revision!==revision){setConfirmation(null);setError(new Error('来源笔记已更新，请重新核对范围后确认。'));return}
    if(!beforeMutation()){setError(new Error('请先保存笔记信息，再开始识别。'));return}
    posting.current=true;setCreating(true);setError(null);setNotice('')
    try {
      // Only this explicit click creates a request. Polls and refreshes are GETs.
      const document=await api<OcrDocument>('/ocr/documents','POST',{note_id:noteId,page_id:page.id,revision:confirmation.revision,request_key:createUuid(),region:confirmation.region,...choice})
      if(!authorized())return
      setConfirmation(null);setSelected(document.id);setOffset(0)
      client.setQueryData([...scope,'document',document.id],document)
      setNotice('已提交一次识别。可继续阅读；重新识别会创建新记录，保留已有纠错。')
      void client.invalidateQueries({queryKey:[...scope,'list']})
    } catch(error) {
      if(!authorized())return
      setConfirmation(null);setError(error)
      setNotice(error instanceof ApiError?'未自动重试。请先刷新识别记录核对状态；再次提交属于新的模型调用。':'提交结果暂时未知，请先刷新识别记录核对。不会自动重发；再次提交属于新的模型调用。')
      void client.invalidateQueries({queryKey:[...scope,'list']})
    } finally {
      posting.current=false
      if(authorized()){setCreating(false);onBusyChange(false)}
    }
  }

  if(!valid)return null
  return <section className="ocr-panel" aria-label="文字识别 OCR">
    <div className="panel-heading"><h3>文字识别 <span className="muted">OCR</span></h3><span className="badge">手动单页 / 单选区</span></div>
    <p className="muted">识别文字与公式后请人工核对。原文和纠错稿分别保存，不会写入笔记正文。每次提交最多发起一次模型调用，可能产生费用。</p>
    <NoteError error={catalog.error}/>
    {catalog.isPending?<p role="status">正在读取共享模型设置…</p>:!configured?<p role="status" className="muted">文字识别暂不可用：共享视觉模型尚未配置，请联系管理员。图片仍可正常阅读。</p>:null}
    <ModelPicker catalog={catalog.data} value={choice} onChange={value=>{setChoice(value);setConfirmation(null)}} vision disabled={!canMutate||creating} label="OCR 识别模型"/>
    <fieldset className="ocr-range" disabled={!canMutate||creating||!!confirmation}>
      <legend>当前所选页面的发送范围</legend>
      <div className="ocr-mode"><label><input type="radio" name={'ocr-mode-'+page.id} checked={mode==='page'} onChange={()=>setMode('page')}/>整页识别</label><label><input type="radio" name={'ocr-mode-'+page.id} checked={mode==='region'} onChange={()=>setMode('region')}/>单个矩形选区</label></div>
      {mode==='region'?<RegionCanvas key={page.file.id} fileId={page.file.id} title={page.file.filename+' · OCR 选区'} region={region} selecting disabled={!canMutate||creating||!!confirmation} onChange={setRegion} onReady={setReady}/>:null}
      <p className="muted">{page.file.filename} · {mode==='region'&&region?describeRegion(region):mode==='page'?'发送当前整页图片，不扩展到其他页面。':'请在图中选择一个矩形区域。'}</p>
    </fieldset>
    {mode==='page'?<p className="muted">确认前请核对上方当前阅读页。只发送此页，不发送整本 PDF。</p>:null}
    <div className="note-actions"><button type="button" className="primary" disabled={!canCreate||!!confirmation} onClick={()=>{setError(null);setConfirmation({revision,region:mode==='region'?region:null})}}>{(attempts.data?.length||selected)?'重新识别（新调用）':'识别本页文字'}</button></div>
    {!canMutate?<p className="muted">请先保存笔记信息，并等待页面操作完成。</p>:null}
    {confirmation?<div className="ocr-confirm" role="group" aria-label="确认发送识别资料"><strong>确认发送当前{confirmation.region?'矩形选区':'整页'}给共享模型？</strong><p>{page.file.filename} · 笔记版本 {confirmation.revision} · {modelLabel(choice.provider,choice.model)}</p>{confirmation.region?<p>{describeRegion(confirmation.region)}</p>:null}<p>这会创建一次新的模型调用，可能产生费用。失败或中断后不会自动重发。已有识别和纠错稿会保留。</p><div className="note-actions"><button type="button" className="primary" disabled={!canCreate} onClick={()=>void createAttempt()}>{creating?'正在提交…':'确认发送并识别'}</button><button type="button" className="secondary" disabled={creating} onClick={()=>setConfirmation(null)}>取消</button></div></div>:null}
    <NoteError error={error}/>{notice?<p role="status" className="muted">{notice}</p>:null}
    <div className="panel-heading"><h4>本页识别记录</h4><button type="button" className="text-button" disabled={attempts.isFetching||creating} onClick={()=>{void attempts.refetch();if(selected)void detail.refetch()}}>刷新识别记录</button></div>
    <NoteError error={attempts.error}/>
    {attempts.isPending?<p role="status">读取识别记录…</p>:!attempts.data?.length?<p className="muted">尚无识别记录。</p>:<ol className="ocr-attempts">{attempts.data.map(document=><li key={document.id}><button type="button" className="secondary" aria-pressed={selected===document.id} onClick={()=>setSelected(document.id)}><strong>{statuses[document.status]||document.status}</strong><span>{date(document.created_at)} · {document.region?'矩形选区':'整页'} · {document.reviewed?'已复核':'未复核'}{!document.source_valid?' · 来源已失效':''}</span></button></li>)}</ol>}
    <div className="note-actions"><button type="button" className="text-button" disabled={!offset||attempts.isFetching} onClick={()=>setOffset(value=>Math.max(0,value-pageSize))}>较新记录</button><button type="button" className="text-button" disabled={attempts.data?.length!==pageSize||attempts.isFetching} onClick={()=>setOffset(value=>value+pageSize)}>较早记录</button></div>
    <NoteError error={detail.error}/>
    {selected&&detail.isPending?<p role="status">读取识别详情…</p>:detail.data&&selected?<OcrResult key={detail.data.id} document={detail.data} page={page} ownerId={ownerId} sessionId={ticket.id} canMutate={canMutate} onSaved={async document=>{await client.cancelQueries({queryKey:detailKey});if(!authorized())return;client.setQueryData(detailKey,document);void client.invalidateQueries({queryKey:[...scope,'list']})}}/>:null}
  </section>
}

function OcrResult({document,page,ownerId,sessionId,canMutate,onSaved}:{document:OcrDocument;page:PageOut;ownerId:string;sessionId:string;canMutate:boolean;onSaved:(document:OcrDocument)=>Promise<void>}) {
  const [draft,setDraft]=useState(()=>draftFrom(document)),[saving,setSaving]=useState(false),[error,setError]=useState<unknown>(null),[notice,setNotice]=useState(''),[showSource,setShowSource]=useState(false),[showPreview,setShowPreview]=useState(false)
  const previewBody=useMemo(()=>showPreview?ocrPreviewBody(draft.text):null,[showPreview,draft.text])
  const savingRef=useRef(false),alive=useRef(true),[ticket]=useState(()=>authSession.ticket())
  const authorized=()=>alive.current&&ticket.id===sessionId&&authSession.current(ticket,ownerId)
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[])
  useEffect(()=>{setDraft(old=>reconcileDraft(old,document))},[document.revision,document.raw_text,document.corrected_text,document.reviewed])
  const sameSource=document.source_valid&&document.page_id===page.id&&document.file_id===page.file.id
  const editable=sameSource&&document.status==='succeeded'&&canMutate
  const conflict=draft.dirty&&draft.revision!==document.revision
  async function save() {
    if(!editable||savingRef.current||!authorized()||!draft.dirty)return
    savingRef.current=true;setSaving(true);setError(null);setNotice('')
    try {
      const updated=await api<OcrDocument>('/ocr/documents/'+document.id,'PATCH',{revision:draft.revision,corrected_text:draft.text,reviewed:draft.reviewed})
      if(!authorized())return
      await onSaved(updated)
      if(!authorized())return
      setDraft(draftFrom(updated));setNotice('纠错稿已保存。')
    } catch(error) {
      if(!authorized())return
      setError(error instanceof ApiError&&error.status===409?new Error('纠错版本冲突：当前草稿仍保留。刷新识别记录查看最新保存内容，核对后可将当前草稿基于最新版本重新保存。'):error)
    } finally {savingRef.current=false;if(authorized())setSaving(false)}
  }
  return <article className="ocr-result" aria-label="识别详情">
    <h4>{statuses[document.status]||document.status}</h4>
    <p className="muted">{document.region?'矩形选区':'整页'} · 提交时页码 {document.page_number} · 来源版本 {document.source_revision} · {modelLabel(document.provider,document.model)} · {date(document.created_at)}</p>
    {!sameSource?<p className="error" role="alert">来源已失效，不能作为当前页的识别内容或保存纠错。{document.source_error?'原因：'+document.source_error:''}</p>:null}
    {document.error_code?<p className="error" role="status">识别错误：{document.error_code}。不会自动重发；请先核对状态，需要时从上方显式发起新调用。</p>:null}
    {isOcrActive(document.status)?<p role="status">正在自动查询进度，不会发起新的识别。可继续阅读原图。</p>:null}
    <button type="button" className="secondary" disabled={!sameSource} onClick={()=>setShowSource(value=>!value)}>{showSource?'收起原页定位':'定位原页 / 选区'}</button>
    {showSource&&sameSource?<div className="ocr-source"><p className="muted">识别时的原页。{document.region?describeRegion(document.region):'本次来源为整页。'}</p><ImageReader key={document.id} fileId={document.file_id} filename={page.file.filename} region={document.region}/></div>:null}
    {document.status==='succeeded'?<><details open><summary>模型识别原文（需核对）</summary><pre className="ocr-transcript">{document.raw_text||'（未识别到文字）'}</pre></details>
      <label>人工纠错稿<textarea aria-label="人工纠错稿" rows={10} maxLength={64000} value={draft.text} disabled={!editable||saving} onChange={event=>{setDraft(old=>({...old,text:event.target.value,dirty:true}));setNotice('')}}/></label>
      <details onToggle={event=>setShowPreview(event.currentTarget.open)}><summary>预览纠错稿中的公式</summary>{previewBody?<Suspense fallback={<p>加载公式预览…</p>}><FormattedAnswer body={previewBody}/></Suspense>:null}<p className="muted">支持 $…$、$$…$$、\(…\) 和 \[…\]；预览不会改变原文或纠错稿。公式仍需核对。</p></details>
      <label className="ocr-reviewed"><input type="checkbox" checked={draft.reviewed} disabled={!editable||saving} onChange={event=>{setDraft(old=>({...old,reviewed:event.target.checked,dirty:true}));setNotice('')}}/>我已人工核对文字与公式</label>
      {conflict?<div className="ocr-conflict" role="alert"><p>服务器纠错版本已变为 {document.revision}，当前草稿仍基于版本 {draft.revision}。轮询不会覆盖草稿。</p><details><summary>查看服务器当前纠错稿</summary><pre className="ocr-transcript">{document.corrected_text??document.raw_text??'（无文字）'}</pre><p>{document.reviewed?'服务器已标记复核':'服务器未标记复核'}</p></details><button type="button" className="secondary" disabled={saving||!editable} onClick={()=>{setDraft(old=>({...old,revision:document.revision}));setError(null);setNotice('保留当前草稿，已采用最新纠错版本；请核对后再次保存。')}}>保留我的草稿并采用最新版本号</button></div>:null}
      <NoteError error={error}/>{notice?<p role="status" className="muted">{notice}</p>:null}<div className="note-actions"><button type="button" className="primary" disabled={!editable||saving||!draft.dirty||conflict} onClick={()=>void save()}>{saving?'保存中…':'保存纠错稿'}</button><span className="muted" role="status">{draft.dirty?'有未保存修改':'已与保存内容同步'} · 纠错版本 {draft.revision}</span></div>
    </>:null}
  </article>
}
