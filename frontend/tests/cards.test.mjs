import assert from 'node:assert/strict'
import {test} from 'node:test'
import {ApiError} from '../src/api.ts'
import {canonicalText} from '../src/features/notes/text-selection.ts'
import {clozeParts,textRange,withinRegion,validateCard,generationAmbiguous,sourceVisual,selectionOnPage,updatePageSelection,allPageSelected} from '../src/features/learning/cards.ts'

test('50 selected snapshots clear when focus refetch changes page membership or order',()=>{
  const page=Array.from({length:50},(_,index)=>({id:'card-'+index,revision:1}))
  const selected={pageIds:page.map(item=>item.id),items:page}
  const newcomer={id:'new-card',revision:1},next=[newcomer,...page.slice(0,49)]
  assert.equal(allPageSelected(selected.items,next),false)
  assert.deepEqual(selectionOnPage(selected,next).items,[])
  const chosen=updatePageSelection(selected,next,old=>[...old,newcomer])
  assert.deepEqual(chosen.items,[newcomer])
  assert.deepEqual(selectionOnPage(selected,[...page].reverse()).items,[])
})
test('same membership refetch preserves stale selected revisions for atomic conflict detection',()=>{
  const old=[{id:'card',revision:2}],state={pageIds:['card'],items:old}
  const current=[{id:'card',revision:3}]
  assert.equal(selectionOnPage(state,current),state)
  assert.equal(selectionOnPage(state,current).items[0].revision,2)
  assert.equal(allPageSelected(old,current),true)
  assert.equal(allPageSelected([old[0],old[0]],current),false)
})
test('selection updates never include another page, duplicate IDs, or more than 50 cards',()=>{
  const page=Array.from({length:51},(_,index)=>({id:'card-'+index,revision:1}))
  const state={pageIds:page.map(item=>item.id),items:page.slice(0,50)}
  assert.equal(updatePageSelection(state,page,old=>[...old,page[50]]).items.length,50)
  assert.deepEqual(updatePageSelection(state,page,()=>[page[0],page[0],{id:'other-page'}]).items,[page[0]])
})

test('saved rich-text projections yield codepoint ranges across astral characters and formulas',()=>{
  const text=canonicalText({type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'A😀中文'}]},{type:'paragraph',content:[{type:'inlineMath',attrs:{latex:'x^2'}}]}]})
  assert.equal(text,'A😀中文\nx^2')
  assert.deepEqual(textRange(text,1,4),{start:1,end:4,quote:'😀中文'})
  assert.deepEqual(textRange(text,5,8),{start:5,end:8,quote:'x^2'})
})
test('invalid text selection never silently trims or truncates frozen source content',()=>{
  for(const [start,end] of [[-1,1],[1,1],[8000,8002],[.5,2],[0,8001]])assert.throws(()=>textRange('😀'.repeat(8001),start,end))
  assert.throws(()=>textRange('   ',0,3))
  assert.equal(textRange('😀'.repeat(8000),0,8000).quote.length,16000)
})
test('cloze separates answers without leaking hidden answers into visible text parts',()=>{
  const parts=clozeParts('水由{{氢😀}}和{{氧}}组成。')
  assert.equal(parts.filter(part=>!part.answer).map(part=>part.text).join(''),'水由和组成。')
  assert.deepEqual(parts.filter(part=>part.answer).map(part=>part.text),['氢😀','氧'])
  for(const value of ['没有填空','{{ }}','{{答案}','{{{答案}}}','{{a}} 后接 {{坏','{{}}'])assert.throws(()=>clozeParts(value))
})
test('mask bounds respect both image and cropped source; nonfinite or zero areas are rejected',()=>{
  const crop={x:.2,y:.3,width:.4,height:.5},valid={x:.3,y:.4,width:.2,height:.2}
  assert.equal(withinRegion(valid,crop),true)
  for(const mask of [{...valid,x:.1},{...valid,y:.1},{...valid,width:.5},{...valid,height:0},{...valid,x:NaN},{...valid,width:Infinity},{x:.9,y:0,width:.2,height:.1}])assert.equal(withinRegion(mask,crop),false)
})
test('type-specific editor validation rejects invisible occlusion and malformed or unbounded content',()=>{
  const mask={x:.1,y:.1,width:.2,height:.3}
  assert.doesNotThrow(()=>validateCard('qa','问题','答案',[],false))
  assert.doesNotThrow(()=>validateCard('formula','x^2','解释',[],false))
  assert.doesNotThrow(()=>validateCard('occlusion','问题','答案',[mask],true))
  assert.throws(()=>validateCard('qa','问题',' ',[],false))
  assert.throws(()=>validateCard('cloze','{{空}}与{{}}','',[],false))
  assert.throws(()=>validateCard('occlusion','问题','答案',[mask],false))
  assert.throws(()=>validateCard('occlusion','问题','答案',Array(21).fill(mask),true))
  assert.throws(()=>validateCard('formula','😀'.repeat(8001),'',[],false))
})
test('ambiguous network, malformed JSON, server errors and timeout retain original paid identity',()=>{
  for(const error of [new TypeError('network'),new SyntaxError('invalid JSON'),new ApiError(500,'unknown','error','id'),new ApiError(408,'timeout','error','id')])assert.equal(generationAmbiguous(error),true)
  for(const status of [400,401,403,404,409,422,429])assert.equal(generationAmbiguous(new ApiError(status,'rejected','error','id')),false)
})
test('public source alone never produces a private image; only fresh source-read file identity does',()=>{
  const source={kind:'pdf',note_id:'note',title:'资料',revision:2,mode:'pages',pages:[{page_id:'page',number:3}]}
  assert.equal(sourceVisual({source,source_changed:true}),null)
  assert.deepEqual(sourceVisual({source,source_changed:true,file_id:'validated-file'}),{fileId:'validated-file',title:'资料',region:null})
  assert.equal(sourceVisual({source:{kind:'text',quote:'文字'},file_id:'unexpected'}),null)
})
