import test from 'node:test'
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {homeSection,shouldApplyHomeDefault,interestTerms,moveTile,unknownSubmission,analysisActive} from '../src/features/personalization/personalization.ts'
import {parseSubmission,submissionStorageKey} from '../src/features/personalization/analysis-submission.ts'
const require=createRequire(new URL('../package.json',import.meta.url))
test('startup default only accepts safe ordinary sections',()=>{
  for(const id of ['home','notes','cards','quizzes','reviews','learning','preferences','social','messages','community','profile','chat'])assert.equal(homeSection(id),id)
  for(const id of ['users','invites','audit','ai','jobs','__proto__','constructor','prototype',null,{},'javascript:alert(1)'])assert.equal(homeSection(id),null)
})
test('late preference response cannot override explicit navigation, restored notes/chat, or an already applied default',()=>{
  const baseline={explicitNavigation:false,restoredNotes:false,restoredChat:false,alreadyApplied:false}
  assert.equal(shouldApplyHomeDefault(baseline),true)
  for(const field of Object.keys(baseline))assert.equal(shouldApplyHomeDefault({...baseline,[field]:true}),false)
})
test('keyboard movement preserves all five tiles, immutable input and bounds',()=>{
  const order=['notes','reviews','social','messages','shares'];const moved=moveTile(order,2,-1)
  assert.deepEqual(moved,['notes','social','reviews','messages','shares']);assert.deepEqual(order,['notes','reviews','social','messages','shares'])
  assert.deepEqual(moveTile(order,0,-1),order);assert.deepEqual(moveTile(order,4,1),order)
  assert.equal(new Set(moved).size,5)
})
test('manual interests normalize separators, never accept duplicate/oversized/unbounded signals',()=>{
  assert.deepEqual(interestTerms('数学， 英语\nAI','subjects'),['数学','英语','AI'])
  assert.deepEqual(interestTerms('','tags'),[])
  for(const input of ['a,a',Array.from({length:21},(_,i)=>'s'+i).join(','),'x'.repeat(81),'a\u0000'])assert.throws(()=>interestTerms(input,'subjects'))
  assert.throws(()=>interestTerms('x'.repeat(65),'tags'));assert.equal(interestTerms('x'.repeat(64),'tags')[0].length,64)
})
test('uncertain transport/timeout/server results retain same request; definitive client errors do not',()=>{
  for(const error of [new Error('network'),{status:500},{status:503},{status:408},null])assert.equal(unknownSubmission(error),true)
  for(const status of [400,401,403,404,409,422,429])assert.equal(unknownSubmission({status}),false)
  assert.equal(analysisActive('queued'),true);assert.equal(analysisActive('running'),true)
  for(const state of ['failed','succeeded','cancelled','interrupted','unknown'])assert.equal(analysisActive(state),false)
})
test('persisted submission contains only exact immutable preview ID/hash/request key and is owner/session scoped',()=>{
  const body={preview_id:'11111111-1111-4111-8111-111111111111',preview_hash:'a'.repeat(64),request_key:'22222222-2222-4222-8222-222222222222'}
  assert.deepEqual(parseSubmission(JSON.stringify(body)),body)
  for(const bad of [{...body,request_key:'bad'},{...body,preview_hash:'bad'},{...body,provider:'injected'},{...body,raw_notes:'secret'}])assert.equal(parseSubmission(JSON.stringify(bad)),null)
  for(const raw of ['null','{}','invalid','[]',null])assert.equal(parseSubmission(raw),null)
  assert.notEqual(submissionStorageKey('alice','s1'),submissionStorageKey('alice','s2'));assert.notEqual(submissionStorageKey('alice','s1'),submissionStorageKey('bob','s1'))
})
test('existing installed Markdown parser creates structured heading/bold/link data for existing safe renderer',()=>{
  const {MarkdownManager}=require('@tiptap/markdown'),{default:StarterKit}=require('@tiptap/starter-kit')
  const body=new MarkdownManager({extensions:[StarterKit]}).parse('# 学习事实\n\n**记录**与[来源](https://example.com)\n\n- 复习一次')
  assert.equal(body.type,'doc');assert.equal(body.content[0].type,'heading')
  const json=JSON.stringify(body);assert.match(json,/'?bold'?/);assert.match(json,/https:\/\/example.com/);assert.match(json,/bulletList/)
})
