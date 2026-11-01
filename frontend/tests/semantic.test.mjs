import assert from 'node:assert/strict'
import {test} from 'node:test'
import {ApiError} from '../src/api.ts'
import {semanticAmbiguous,semanticRequest,semanticReason,semanticUsage} from '../src/features/search/semantic.ts'

const filters={q:'  😀学习方法  ',kind:'image',folder_id:'folder',notebook_id:'book',tag_id:'tag'}
test('semantic query preserves Unicode, explicit consent, request key and complete scope',()=>{
  assert.deepEqual(semanticRequest(filters,'fixed-key'),{request_key:'fixed-key',q:'😀学习方法',confirm_external:true,limit:20,kind:'image',folder_id:'folder',notebook_id:'book',tag_id:'tag'})
  assert.equal(semanticRequest({...filters,q:'😀'.repeat(100)},'key').q.length,200)
})
test('empty, overlong and invalid-kind queries cannot form provider requests',()=>{
  for(const q of ['  ','😀'.repeat(101)])assert.throws(()=>semanticRequest({...filters,q},'key'))
  assert.throws(()=>semanticRequest({...filters,kind:'pdf'},'key'))
})
test('empty filters are omitted; a deliberate retry can retain the identical payload',()=>{
  const value={...filters,kind:'',folder_id:'',notebook_id:'',tag_id:''}
  assert.deepEqual(semanticRequest(value,'same-key'),{request_key:'same-key',q:'😀学习方法',confirm_external:true,limit:20})
  assert.deepEqual(semanticRequest(value,'same-key'),semanticRequest(value,'same-key'))
})
test('disabled or unavailable service consistently guides back to keyword search',()=>{
  const state={enabled:true,configured:true,available:true,active_index_id:null,building_index_id:null,config:null,reason:null}
  for(const patch of [{enabled:false},{configured:false},{available:false}])assert.match(semanticReason({...state,...patch}),/关键词搜索/)
  assert.match(semanticReason(state),/激活/)
  assert.match(semanticReason({...state,active_index_id:'id'}),/可用/)
})
test('recorded usage is distinguishable from unreported usage',()=>{
  assert.match(semanticUsage({}),/暂无/)
  assert.equal(semanticUsage({input_tokens:128}), 'input_tokens: 128')
})

test('ambiguous transport, timeout and server failures retain the original paid request identity',()=>{
  for(const error of [new TypeError('fetch failed'),new SyntaxError('invalid response'),new ApiError(500,'unknown','server error','id'),new ApiError(503,'unknown','unavailable','id'),new ApiError(408,'timeout','timeout','id')])assert.equal(semanticAmbiguous(error),true)
  for(const status of [400,401,403,404,409,422,429])assert.equal(semanticAmbiguous(new ApiError(status,'rejected','rejected','id')),false)
})
