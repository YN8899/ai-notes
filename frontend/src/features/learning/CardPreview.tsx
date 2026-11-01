import {useEffect,useRef,useState,type PointerEvent} from 'react'
import {useQuery} from '@tanstack/react-query'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import {api} from '../../api'
import {PrivateImage} from '../files/PrivateImage'
import {NoteError} from '../notes/NoteError'
import {dragRegion,imagePoint,type Region} from '../ai/image-region'
import {RegionFields} from '../ai/RegionSelection'
import {clozeParts,sourceVisual,withinRegion,wholePage,type Card,type Kind,type SourceRead,type Visual} from './cards'
const noMasks:Region[]=[]

function Formula({value}:{value:string}){
  try{const html=katex.renderToString(value,{displayMode:true,throwOnError:true,trust:false,strict:'ignore',maxExpand:1000,maxSize:10});return <div className="card-formula" dangerouslySetInnerHTML={{__html:html}}/>}
  catch{return <div role="note"><p className="muted">公式无法排版，请检查 LaTeX：</p><code>{value}</code></div>}
}
/** Rectangles use actual image pixels, including when a narrow image is centered. */
function MaskImage({visual,masks,revealed,editing,onDraw}:{visual:Visual;masks:Region[];revealed:boolean;editing?:boolean;onDraw?:(mask:Region)=>void}){
  const [image,setImage]=useState<HTMLImageElement|null>(null),[geometry,setGeometry]=useState<{left:number;top:number;width:number;height:number}|null>(null),[draft,setDraft]=useState<Region|null>(null),[error,setError]=useState('')
  const start=useRef<{pointer:number;x:number;y:number}|null>(null)
  useEffect(()=>{if(!image){setGeometry(null);return}const measure=()=>setGeometry({left:image.offsetLeft,top:image.offsetTop,width:image.offsetWidth,height:image.offsetHeight});const observer=new ResizeObserver(measure);observer.observe(image);if(image.offsetParent)observer.observe(image.offsetParent);measure();return()=>observer.disconnect()},[image])
  function down(e:PointerEvent<HTMLDivElement>){if(!editing||!image||!e.isPrimary||e.button!==0||(e.target as HTMLElement).closest('button'))return;const rect=image.getBoundingClientRect();if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom)return;const point=imagePoint({x:e.clientX,y:e.clientY},rect);start.current={pointer:e.pointerId,...point};e.currentTarget.setPointerCapture(e.pointerId);e.preventDefault();setError('')}
  function move(e:PointerEvent<HTMLDivElement>){if(start.current?.pointer===e.pointerId&&image)setDraft(dragRegion(start.current,imagePoint({x:e.clientX,y:e.clientY},image.getBoundingClientRect())))}
  function up(e:PointerEvent<HTMLDivElement>){if(start.current?.pointer!==e.pointerId||!image)return;const mask=dragRegion(start.current,imagePoint({x:e.clientX,y:e.clientY},image.getBoundingClientRect()));start.current=null;setDraft(null);if(mask&&withinRegion(mask,visual.region||wholePage))onDraw?.(mask);else setError('遮挡框须位于来源选区内，宽高必须大于 0。');if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId)}
  const rectStyle=(mask:Region)=>geometry?{left:geometry.left+mask.x*geometry.width,top:geometry.top+mask.y*geometry.height,width:mask.width*geometry.width,height:mask.height*geometry.height}:{}
  return <><div className={'card-mask-stage '+(editing?'drawing':'')} aria-label={editing?'拖动绘制遮挡框':'图片遮挡预览'} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={()=>{start.current=null;setDraft(null)}}>
    <PrivateImage key={visual.fileId} fileId={visual.fileId} kind="reading" alt={visual.title} onImageReady={setImage}/>
    {geometry&&visual.region?<span className="card-scope" aria-label="授权来源选区" style={rectStyle(visual.region)}/>:null}
    {geometry&&(!revealed||editing)?masks.map((mask,index)=><span className={'card-mask '+(editing?'editable':'')} aria-label={'遮挡区域 '+(index+1)} key={index} style={rectStyle(mask)}>{editing?index+1:null}</span>):null}
    {geometry&&draft?<span className="card-mask draft" style={rectStyle(draft)}/>:null}
  </div>{error?<p className="error" role="alert">{error}</p>:null}</>
}
export function MaskEditor({visual,masks,onChange}:{visual:Visual;masks:Region[];onChange:(masks:Region[])=>void}){
  const [selected,setSelected]=useState(0),[error,setError]=useState('')
  function append(mask:Region){if(masks.length>=20){setError('每张卡片最多 20 个遮挡框。');return}setSelected(masks.length);onChange([...masks,mask]);setError('')}
  function replace(mask:Region){if(!withinRegion(mask,visual.region||wholePage)){setError('遮挡框超出授权来源选区。');return}onChange(masks.map((old,index)=>index===selected?mask:old));setError('')}
  const active=masks[selected]
  return <section className="card-mask-editor" aria-label="遮挡区域编辑器"><h4>遮挡区域 · {masks.length} / 20</h4><p className="muted">在图片上拖动画框；绿色边框是授权来源范围。每个黑色区域在正面隐藏，查看答案后解除。</p>
    <MaskImage visual={visual} masks={masks} revealed={false} editing onDraw={append}/>
    <div className="card-actions"><button type="button" className="secondary" disabled={masks.length>=20} onClick={()=>{const r=visual.region||wholePage;append({x:r.x+r.width*.25,y:r.y+r.height*.25,width:r.width*.5,height:r.height*.5})}}>添加居中遮挡框</button>{masks.length?<label>调整哪个框<select aria-label="选择遮挡框" value={Math.min(selected,masks.length-1)} onChange={e=>setSelected(Number(e.target.value))}>{masks.map((_,index)=><option key={index} value={index}>遮挡框 {index+1}</option>)}</select></label>:null}<button type="button" className="text-button" disabled={!active} onClick={()=>{onChange(masks.filter((_,index)=>index!==selected));setSelected(0)}}>移除选中遮挡框</button></div>
    {active?<RegionFields key={selected} region={active} onChange={replace} disabled={false}/>:null}<NoteError error={error}/>
  </section>
}
export function CardPreview({kind,front,back,masks=noMasks,visual}:{kind:Kind;front:string;back:string;masks?:Region[];visual?:Visual|null}){
  const [revealed,setRevealed]=useState(false)
  useEffect(()=>setRevealed(false),[kind,front,back,visual?.fileId,masks])
  let content
  if(kind==='formula')content=<Formula value={front}/>
  else if(kind==='cloze'){try{content=<p className="card-cloze">{clozeParts(front).map((part,index)=>part.answer?<span key={index} className={revealed?'card-answer':'card-blank'}>{revealed?part.text:'______'}</span>:part.text)}</p>}catch(e){content=<p className="muted">{e instanceof Error?e.message:'请填写有效填空'}</p>}}
  else content=<p className="card-copy">{front||'填写正面内容以预览'}</p>
  return <section className="card-preview" aria-label="卡片正反预览"><div className="panel-heading"><h4>{revealed?'答案':'正面'}</h4><button type="button" className="secondary" onClick={()=>setRevealed(old=>!old)}>{revealed?'返回正面':'查看答案'}</button></div>{content}
    {kind==='occlusion'?(visual?<MaskImage visual={visual} masks={masks} revealed={revealed}/>:<p className="muted">核验有效图片来源后才能展示遮挡。</p>):null}
    {revealed?<div className="card-back">{back||'无补充说明'}</div>:null}
  </section>
}
export function CardSource({owner,sessionId,card,onOpenNote,onRead}:{owner:string;sessionId:string;card:Card;onOpenNote:(id:string)=>void;onRead?:(read:SourceRead|null)=>void}){
  const source=useQuery({queryKey:['card-source',owner,sessionId,card.id,card.revision],queryFn:()=>api<SourceRead>('/learning/cards/'+card.id+'/source'),enabled:!!card.source,retry:false,gcTime:0,refetchOnMount:'always'})
  // Failed or in-flight validation never leaves a previous image available to the editor.
  const read=source.isFetching||source.isError?null:source.data||null
  useEffect(()=>{onRead?.(read)},[read,onRead])
  if(!card.source)return <p className="muted">未关联来源。</p>
  const visual=read?sourceVisual(read):null
  return <section className="card-source-read" aria-label="卡片来源"><h4>来源 · {card.source.title}</h4>{source.isFetching?<p role="status">正在重新核验来源…</p>:source.isError?<><p className="error" role="alert">来源已删除、失效或暂时不可读取。卡片内容仍然保留；不会请求旧页图片。</p><NoteError error={source.error}/><button type="button" className="secondary" onClick={()=>void source.refetch()}>重新核验来源</button></>:read?<><p className="muted">{read.source_changed?'历史来源：当前笔记已变化，以下对应生成时保存版本':'来源版本'} {read.source.revision}{read.source.kind!=='text'?' · 第 '+read.source.pages.map(page=>page.number).join('、')+' 页':''}</p>{read.source.kind==='text'?<blockquote className="card-quote">{read.text||read.source.quote}</blockquote>:visual?<details><summary>查看未遮挡来源原图</summary><div className="card-source-image"><PrivateImage fileId={visual.fileId} kind="reading" alt={visual.title}/></div></details>:null}<button type="button" className="text-button" onClick={()=>onOpenNote(read.source.note_id)}>打开当前笔记</button></>:null}</section>
}
