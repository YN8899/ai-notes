import {useCallback,useEffect,useRef,useState,type FormEvent} from 'react'
import {api,ApiError} from '../../api'
import {authSession} from '../../session'
import {NoteError} from '../notes/NoteError'
import {CardSourcePicker} from './CardSourcePicker'
import {CardPreview,CardSource,MaskEditor} from './CardPreview'
import {kindNames,sourceVisual,statusNames,validateCard,type Card,type Deck,type Kind,type Selection,type SourceRead,type Visual} from './cards'
import type {Region} from '../ai/image-region'

export function CardEditor({owner,sessionId,card,decks,onClose,onSaved,onOpenNote}:{owner:string;sessionId:string;card?:Card;decks:Deck[];onClose:()=>void;onSaved:(card:Card)=>void;onOpenNote:(id:string)=>void}){
  const [ticket]=useState(()=>authSession.ticket()),[deckId,setDeckId]=useState(card?.deck_id||decks.find(deck=>deck.status==='active')?.id||''),[kind,setKind]=useState<Kind>(card?.kind||'qa')
  const [front,setFront]=useState(card?.front||''),[back,setBack]=useState(card?.back||''),[status,setStatus]=useState<Card['status']>(card?.status||'draft'),[masks,setMasks]=useState<Region[]>(card?.payload?.masks||[])
  const [replace,setReplace]=useState(!card?.source),[source,setSource]=useState<Selection|null>(null),[chosenVisual,setChosenVisual]=useState<Visual|null>(null),[read,setRead]=useState<SourceRead|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null)
  const host=useRef<HTMLElement>(null)
  useEffect(()=>{host.current?.focus({preventScroll:true});host.current?.scrollIntoView({block:'start'})},[])
  const onSource=useCallback((value:Selection|null,visual:Visual|null)=>{setSource(value);setChosenVisual(visual)},[])
  const onRead=useCallback((value:SourceRead|null)=>setRead(value),[])
  const visual=replace?chosenVisual:read?sourceVisual(read):null
  const active=()=>authSession.current(ticket,owner)&&ticket.id===sessionId
  async function save(e:FormEvent){
    e.preventDefault();if(busy)return
    try{validateCard(kind,front,back,masks,!!visual,visual?.region||undefined);if(!deckId)throw new Error('请先选择可用卡片集。')}catch(e){setError(e);return}
    setBusy(true);setError(null)
    const body={deck_id:deckId,kind,front,back,status,payload:kind==='occlusion'?{masks}:null,...(replace?{source}:{}),...(card?{revision:card.revision}:{})}
    try{const result=await api<Card>('/learning/cards'+(card?'/'+card.id:''),card?'PATCH':'POST',body);if(active())onSaved(result)}catch(e){if(active())setError(e)}finally{if(active())setBusy(false)}
  }
  return <section ref={host} tabIndex={-1} className="panel card-editor" aria-label={card?'编辑卡片':'新建卡片'}><div className="panel-heading"><h3>{card?'编辑卡片':'新建卡片'}</h3><button type="button" className="text-button" disabled={busy} onClick={onClose}>关闭编辑</button></div>
    <form onSubmit={save}><fieldset disabled={busy}><div className="card-filters"><label>所属卡片集<select aria-label="所属卡片集" value={deckId} onChange={e=>setDeckId(e.target.value)} required><option value="">请选择卡片集</option>{decks.filter(deck=>deck.status==='active'||deck.id===card?.deck_id).map(deck=><option key={deck.id} value={deck.id}>{deck.name}{deck.status==='archived'?'（已归档，请恢复后保存）':''}</option>)}</select></label><label>卡片类型<select aria-label="卡片类型" value={kind} onChange={e=>setKind(e.target.value as Kind)}>{Object.entries(kindNames).map(([value,name])=><option key={value} value={value}>{name}</option>)}</select></label><label>保存状态<select aria-label="保存状态" value={status} onChange={e=>setStatus(e.target.value as Card['status'])}>{Object.entries(statusNames).map(([value,name])=><option key={value} value={value}>{name}</option>)}</select></label></div>
      <label>{kind==='formula'?'正面 LaTeX':kind==='cloze'?'正面（用 {{答案}} 标记填空）':'正面内容'}<textarea aria-label="卡片正面" value={front} onChange={e=>setFront(e.target.value)} required rows={4}/></label><small className="muted">{Array.from(front).length} / 8000 字符</small>
      <label>{kind==='qa'?'答案（必填）':'答案 / 补充说明'}<textarea aria-label="卡片答案" value={back} onChange={e=>setBack(e.target.value)} required={kind==='qa'} rows={3}/></label><small className="muted">{Array.from(back).length} / 8000 字符</small>
      {card?.source?<><CardSource owner={owner} sessionId={sessionId} card={card} onOpenNote={onOpenNote} onRead={onRead}/><label className="card-check"><input type="checkbox" checked={replace} onChange={e=>setReplace(e.target.checked)}/>更换 / 移除来源（未勾选时保留历史来源）</label></>:null}
      {replace?<CardSourcePicker owner={owner} sessionId={sessionId} onChange={onSource} disabled={busy} required={kind==='occlusion'}/>:null}
      {kind==='occlusion'?(visual?<MaskEditor visual={visual} masks={masks} onChange={setMasks}/>:<p className="muted">请先选择并核验图片或单页 PDF 来源。</p>):null}
      <CardPreview kind={kind} front={front} back={back} masks={masks} visual={visual}/>
      <NoteError error={error}/>{error instanceof ApiError&&error.status===409?<p className="error">保存冲突，未覆盖任何新版本。请保留需要的文字，再关闭编辑并刷新卡片后重开。</p>:null}
      <div className="card-actions"><button className="primary" disabled={busy||decks.find(deck=>deck.id===deckId)?.status!=='active'}>{busy?'正在保存…':status==='confirmed'?'保存并明确确认':'保存'+statusNames[status]}</button><button type="button" className="text-button" onClick={onClose}>取消</button></div>
    </fieldset></form>
  </section>
}
