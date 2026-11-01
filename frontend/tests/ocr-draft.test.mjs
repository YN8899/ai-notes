import assert from 'node:assert/strict'
import {test} from 'node:test'
import {draftFrom,isOcrActive,reconcileDraft} from '../src/features/search/ocr-draft.ts'

const document={revision:1,raw_text:'模型原文',corrected_text:null,reviewed:false}
test('polling never overwrites local edits or silently advances their conflict revision',()=>{
  const draft={...draftFrom(document),text:'我的纠错',reviewed:true,dirty:true}
  const remote={...document,revision:2,corrected_text:'另一窗口纠错'}
  assert.equal(reconcileDraft(draft,remote),draft)
  assert.equal(draft.revision,1)
  assert.equal(draft.text,'我的纠错')
})
test('clean drafts follow server corrections, including intentionally empty corrections',()=>{
  assert.equal(draftFrom({...document,corrected_text:''}).text,'')
  assert.deepEqual(reconcileDraft(draftFrom(document),{...document,revision:2,corrected_text:'已保存',reviewed:true}),{revision:2,text:'已保存',reviewed:true,dirty:false})
  assert.equal(draftFrom({...document,raw_text:null}).text,'')
})
test('only active attempts are polled; terminal failure, truncation and interruption do not retry',()=>{
  assert.equal(isOcrActive('queued'),true)
  assert.equal(isOcrActive('running'),true)
  for(const status of ['succeeded','failed','truncated','interrupted','cancelled'])assert.equal(isOcrActive(status),false)
})
