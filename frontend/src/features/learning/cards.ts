import type {components} from '../../api.generated'
import {ApiError} from '../../api.ts'
import type {Region} from '../ai/image-region'

export type Card=components['schemas']['CardOut']
export type Deck=components['schemas']['DeckOut']
export type CardList=components['schemas']['CardList']
export type DeckList=components['schemas']['DeckList']
export type Kind=Card['kind']
export type CardStatus=Card['status']
export type Selection=NonNullable<components['schemas']['CardCreate']['source']>
export type SourceRead=components['schemas']['SourceRead']
export type Generation=components['schemas']['app__modules__learning__card_schemas__GenerationOut']
export type GenerationList=components['schemas']['app__modules__learning__card_schemas__GenerationList']
export type GenerationPreview=components['schemas']['app__modules__learning__card_schemas__GenerationPreviewOut']
export type GenerationRequest=components['schemas']['app__modules__learning__card_schemas__GenerationCreate']
export type PreviewRequest=components['schemas']['app__modules__learning__card_schemas__GenerationPreview']
export type Visual={fileId:string;title:string;region:Region|null}
export const kindNames:Record<Kind,string>={qa:'问答',cloze:'填空',occlusion:'图片遮挡',formula:'公式'}
export const statusNames:Record<CardStatus,string>={draft:'草稿',confirmed:'已确认',archived:'已归档'}
export const wholePage:Region={x:0,y:0,width:1,height:1}

export type PageSelection<T extends {id:string}>={pageIds:string[];items:T[]}
/** Membership/order changes invalidate selection; revision-only refreshes retain its snapshots. */
export function selectionOnPage<T extends {id:string}>(selection:PageSelection<T>,page:T[]):PageSelection<T>{
  const pageIds=page.map(item=>item.id)
  return selection.pageIds.length===pageIds.length&&selection.pageIds.every((id,index)=>id===pageIds[index])?selection:{pageIds,items:[]}
}
export function updatePageSelection<T extends {id:string}>(selection:PageSelection<T>,page:T[],update:T[]|((items:T[])=>T[])):PageSelection<T>{
  const scoped=selectionOnPage(selection,page),allowed=new Set(scoped.pageIds),seen=new Set<string>()
  const items=(typeof update==='function'?update(scoped.items):update).filter(item=>{if(!allowed.has(item.id)||seen.has(item.id))return false;seen.add(item.id);return true})
  return items.length<=50?{...scoped,items}:scoped
}
export function allPageSelected(selected:{id:string}[],page:{id:string}[]):boolean {
  const ids=new Set(selected.map(item=>item.id))
  return page.length>0&&ids.size===selected.length&&ids.size===page.length&&page.every(item=>ids.has(item.id))
}

/** Match server Unicode codepoint ranges, rather than textarea UTF-16 offsets. */
export function textRange(text:string,start:number,end:number){
  const chars=Array.from(text)
  if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||end<=start||end>chars.length||end-start>8000)throw new Error('请选择连续的 1–8000 个字符，结束位置不能超过正文。')
  const quote=chars.slice(start,end).join('')
  if(!quote.trim())throw new Error('选段不能只有空白。')
  return {start,end,quote}
}
export type ClozePart={text:string;answer:boolean}
export function clozeParts(value:string):ClozePart[]{
  const pattern=/(?<!\{)\{\{([^{}]*)\}\}(?!\})/g,parts:ClozePart[]=[]
  let end=0,match:RegExpExecArray|null
  while((match=pattern.exec(value))){if(!match[1].trim())throw new Error('填空答案不能为空。');if(match.index>end)parts.push({text:value.slice(end,match.index),answer:false});parts.push({text:match[1],answer:true});end=pattern.lastIndex}
  if(end<value.length)parts.push({text:value.slice(end),answer:false})
  if(!parts.some(part=>part.answer)||parts.some(part=>!part.answer&&(part.text.includes('{{')||part.text.includes('}}'))))throw new Error('填空需要成对、非空的 {{答案}} 标记。')
  return parts
}
export function withinRegion(mask:Region,scope:Region=wholePage):boolean {
  return [mask.x,mask.y,mask.width,mask.height].every(Number.isFinite)&&mask.width>0&&mask.height>0&&mask.x>=scope.x&&mask.y>=scope.y&&mask.x+mask.width<=scope.x+scope.width+1e-9&&mask.y+mask.height<=scope.y+scope.height+1e-9&&mask.x+mask.width<=1&&mask.y+mask.height<=1
}
export function validateCard(kind:Kind,front:string,back:string,masks:Region[],visual:boolean,scope:Region=wholePage){
  if(!front.trim()||Array.from(front).length>8000||Array.from(back).length>8000||front.includes('\0')||back.includes('\0'))throw new Error('正面必填，每面最多 8000 个字符，不能包含空字符。')
  if(kind==='qa'&&!back.trim())throw new Error('请填写问答卡片的答案。')
  if(kind==='cloze')clozeParts(front)
  if(kind==='occlusion'&&(!visual||masks.length<1||masks.length>20||masks.some(mask=>!withinRegion(mask,scope))))throw new Error('遮挡卡片需要有效图片来源，且 1–20 个遮挡框必须位于授权来源选区内。')
}
export function generationAmbiguous(error:unknown){return !(error instanceof ApiError)||error.status>=500||error.status===408}
export function sourceVisual(read:SourceRead):Visual|null {return read.file_id&&read.source.kind!=='text'?{fileId:read.file_id,title:read.source.title,region:read.source.pages[0]?.region||null}:null}
