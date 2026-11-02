import test from 'node:test'
import assert from 'node:assert/strict'
import {parseTags,publicAssetPath,contentOf,editorBody} from './types.ts'
import {authSession} from '../../session.ts'
import {readEditingDraft,writeEditingDraft} from './draftCache.ts'

test('tags accept Chinese separators and reject duplicate or oversized metadata',()=>{
  assert.deepEqual(parseTags('数学， 公式,复习'),['数学','公式','复习'])
  assert.throws(()=>parseTags('数学,数学'))
  assert.throws(()=>parseTags('a'.repeat(33)))
  assert.throws(()=>parseTags(Array.from({length:13},(_,i)=>String(i)).join(',')))
})
test('community assets cannot point at private file or source-note endpoints',()=>{
  const path='/community/posts/aa-bb/assets/cc-dd?kind=original'
  assert.equal(publicAssetPath('/api/v1'+path),path)
  for(const value of ['/files/private','https://example.com/community/posts/a/assets/b','/api/v1/notes/a','/community/posts/a/assets/b/../../files/a'])assert.throws(()=>publicAssetPath(value))
})
test('draft save payload excludes publication and moderation state',()=>{
  const value=contentOf({title:'标题',body_json:{},plain_text:'',subject:'',post_type:'note',tags:['数学'],source_note_id:null,source_revision:null,revision:9,status:'hidden',moderation_reason:'原因'})
  assert.equal('revision' in value,false)
  assert.equal('status' in value,false)
  assert.equal('moderation_reason' in value,false)
  assert.deepEqual(value.tags,['数学'])
})
test('plain-only source notes remain visible and editable without interpreting text as HTML',()=>{
  assert.deepEqual(editorBody({},'<script>\n第二行'),{type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'<script>'}]},{type:'paragraph',content:[{type:'text',text:'第二行'}]}]})
  const rich={type:'doc',content:[]}
  assert.equal(editorBody(rich,'fallback'),rich)
})
test('unsaved community drafts survive navigation but never account changes or logout',()=>{
  authSession.verify('alice',authSession.ticket())
  const draft={base:null,content:{title:'仅自己可见'},tags:''}
  writeEditingDraft('alice','new',draft)
  assert.equal(readEditingDraft('alice','new'),draft)
  assert.equal(readEditingDraft('bob','new'),undefined)
  authSession.verify('bob',authSession.ticket())
  authSession.verify('alice',authSession.ticket())
  assert.equal(readEditingDraft('alice','new'),undefined)
  writeEditingDraft('alice','new',draft)
  authSession.invalidate(authSession.ticket())
  authSession.verify('alice',authSession.reset())
  assert.equal(readEditingDraft('alice','new'),undefined)
})
