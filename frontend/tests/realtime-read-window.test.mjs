import test from 'node:test'
import assert from 'node:assert/strict'
import {pathToFileURL,fileURLToPath} from 'node:url'
import path from 'node:path'
const source=process.env.T34_NOTIFICATION_SOURCE?path.join(process.env.T34_NOTIFICATION_SOURCE,'feed.ts'):fileURLToPath(new URL('../src/features/notifications/feed.ts',import.meta.url))
const {refreshReadWindow}=await import(pathToFileURL(source))
const row=seq=>({id:String(seq),seq,message:'synthetic',read_at:null,created_at:'2026-11-01T00:00:00Z',target:null})
const state=()=>({items:Array.from({length:100},(_,i)=>row(275-i)),after:275,head:275,revision:0,pending:false})
const page=()=>({items:Array.from({length:100},(_,i)=>row(176+i)),next_after_seq:275,head_seq:275,read_revision:1,has_more:false})
test('same-timestamp seq window refreshes a cached read without legacy ordering',()=>{
  const current=page();current.items[1]={...current.items[1],read_at:'2026-11-01T00:01:00Z'}
  const result=refreshReadWindow(state(),current)
  assert.ok(result.items.find(row=>row.seq===177).read_at)
  assert.equal(result.after,275);assert.equal(result.revision,1);assert.equal(result.items.length,100)
})
test('concurrent creations request follow-up catch-up without acknowledging unseen rows',()=>{
  const result=refreshReadWindow(state(),{...page(),head_seq:280,has_more:true})
  assert.equal(result.after,275);assert.equal(result.head,280);assert.equal(result.pending,true)
  assert.equal(result.items[0].seq,275)
})
test('a missing or replaced item resets to zero rather than accepting a fresh revision',()=>{
  for(const current of [{...page(),items:page().items.filter(row=>row.seq!==177)},
    {...page(),items:page().items.map(row=>row.seq===177?{...row,id:'replaced'}:row)}]){
    const result=refreshReadWindow(state(),current)
    assert.equal(result.after,0);assert.equal(result.revision,0);assert.equal(result.pending,true);assert.deepEqual(result.items,[])
  }
})
