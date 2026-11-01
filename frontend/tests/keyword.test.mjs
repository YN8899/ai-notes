import assert from 'node:assert/strict'
import {test} from 'node:test'
import {highlightedParts,nextSearchPage,searchParams,searchScope} from '../src/features/search/keyword.ts'

test('code point highlighting preserves emoji, combining marks, CJK and literal markup',()=>{
  assert.deepEqual(highlightedParts('😀前𠮷中文<img>尾',3,5),['😀前𠮷','中文','<img>尾'])
  assert.deepEqual(highlightedParts('e\u0301😀目标',2,3),['e\u0301','😀','目标'])
  assert.deepEqual(highlightedParts('<script>学习</script>',8,10),['<script>','学习','</script>'])
})

test('invalid offsets preserve complete text without misleading partial highlights',()=>{
  for(const [start,end] of [[-1,2],[1,20],[2,1],[0,0],[.5,1],[NaN,1],[0,Infinity]])assert.deepEqual(highlightedParts('😀中文',start,end),['😀中文','',''])
})

test('literal query is URL encoded and all filters survive pagination',()=>{
  const filters={q:'  中%_\\😀 &  ',kind:'image',folder_id:'folder',notebook_id:'book',tag_id:'tag'}
  const params=searchParams(filters,'opaque+/=')
  assert.deepEqual(Object.fromEntries(params),{q:'中%_\\😀 &',limit:'20',kind:'image',folder_id:'folder',notebook_id:'book',tag_id:'tag',cursor:'opaque+/='})
  assert.equal(new URLSearchParams(params.toString()).get('q'),'中%_\\😀 &')
  assert.equal(searchParams({...filters,kind:'',folder_id:'',notebook_id:'',tag_id:''}).has('kind'),false)
})

test('scope changes discard navigation for every query/filter/owner/session transition',()=>{
  const filters={q:'中文',kind:'',folder_id:'',notebook_id:'',tag_id:''},base=searchScope('owner','session',filters)
  for(const key of Object.keys(filters))assert.notEqual(searchScope('owner','session',{...filters,[key]:'changed'}),base)
  assert.notEqual(searchScope('other','session',filters),base)
  assert.notEqual(searchScope('owner','new-session',filters),base)
  assert.equal(searchScope('owner','session',{...filters,q:' 中文 '}),base)
  const first=[''],second=nextSearchPage(first,'cursor1'),third=nextSearchPage(second,'cursor2')
  assert.deepEqual(first,['']);assert.deepEqual(third,['','cursor1','cursor2'])
  assert.deepEqual(third.slice(0,-1),second)
  assert.equal(nextSearchPage(second,null),second)
  assert.equal(nextSearchPage(second,'cursor1'),second)
})
