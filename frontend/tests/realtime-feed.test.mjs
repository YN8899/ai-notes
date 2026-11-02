import test from 'node:test'
import assert from 'node:assert/strict'
import {advanceFeed,emptyFeed} from '../src/features/notifications/feed.ts'

const page=(start,count,head,more)=>({items:Array.from({length:count},(_,index)=>({id:String(start+index),seq:start+index,message:'synthetic'})),next_after_seq:start+count-1,has_more:more,head_seq:head,read_revision:0})
test('consumes all 275 durable rows in >100 backlog while bounding visible notices',()=>{
  let state=emptyFeed()
  state=advanceFeed(state,page(1,100,275,true));assert.equal(state.after,100)
  state=advanceFeed(state,page(101,100,275,true));assert.equal(state.after,200)
  state=advanceFeed(state,page(201,75,275,false))
  assert.equal(state.after,275);assert.equal(state.pending,false)
  assert.equal(state.items.length,100);assert.equal(state.items[0].seq,275)
})
test('rejects missing page, duplicate page, unsafe cursor and empty infinite pagination',()=>{
  const initial=advanceFeed(emptyFeed(),page(1,10,20,true))
  assert.throws(()=>advanceFeed(initial,page(12,1,20,false)),/缺口/)
  assert.throws(()=>advanceFeed(initial,page(1,10,20,false)))
  assert.throws(()=>advanceFeed(initial,{...page(11,1,20,false),head_seq:Number.MAX_SAFE_INTEGER+1}))
  assert.throws(()=>advanceFeed(initial,{items:[],next_after_seq:10,has_more:true,head_seq:20,read_revision:0}))
})
test('read revision advances without changing creation cursor',()=>{
  const initial=advanceFeed(emptyFeed(),page(1,10,10,false))
  const changed=advanceFeed(initial,{items:[],next_after_seq:10,has_more:false,head_seq:10,read_revision:4})
  assert.equal(changed.after,10);assert.equal(changed.revision,4)
})
