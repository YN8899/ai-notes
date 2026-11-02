import test from 'node:test'
import assert from 'node:assert/strict'
import {mergeMessages,visibleSequence,queryString} from '../src/features/social/social.ts'

test('reconnected message pages merge by sequence without duplicating acknowledged sends',()=>{
  const message=(seq,id)=>({seq,id,body:String(seq)})
  assert.deepEqual(mergeMessages([message(2,'b'),message(1,'a')],[message(2,'b'),message(3,'c')]).map(m=>m.id),['a','b','c'])
})
test('server acknowledgement replaces local copy with authoritative attachment metadata',()=>{
  const old={seq:1,id:'a',attachments:[]},latest={...old,attachments:[{id:'file'}]}
  assert.deepEqual(mergeMessages([old],[latest]),[latest])
})
test('member searches and pagination preserve punctuation without injecting query parameters',()=>{
  const query=new URLSearchParams(queryString({q:'a&cursor=other',cursor:'signed+/=',limit:20}).slice(1))
  assert.equal(query.get('q'),'a&cursor=other')
  assert.equal(query.get('cursor'),'signed+/=')
  assert.equal(query.get('limit'),'20')
  assert.equal(queryString({q:'',cursor:undefined}),'')
})
test('mark read never skips unseen history when a new own message appears beyond a page gap',()=>{
  const firstPage=Array.from({length:100},(_,i)=>({seq:i+1}))
  assert.equal(visibleSequence([...firstPage,{seq:201}]),100)
  assert.equal(visibleSequence(Array.from({length:201},(_,i)=>({seq:i+1}))),201)
  assert.equal(visibleSequence([{seq:9}]),0)
})
