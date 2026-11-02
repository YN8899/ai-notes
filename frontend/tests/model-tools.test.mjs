import test from 'node:test'
import assert from 'node:assert/strict'
import {modelKey,modelReady,modelLabel} from '../src/features/ai/model-catalog.ts'
import {validLearningDates,validateTools,previewMatches} from '../src/features/ai/tool-contract.ts'
const makeModel=(id,extra={})=>({id,name:id,supports_text:true,supports_vision:false,supports_tools:false,supports_streaming:true,supports_embedding:false,...extra})
const catalog={credential_source:'shared',platform_enabled:true,providers:[{id:'deepseek',name:'DeepSeek',enabled:true,models:[makeModel('deepseek-flash',{supports_vision:true})]},{id:'custom',name:'Custom',enabled:true,models:[makeModel('deepseek-flash',{supports_tools:true}),makeModel('original/'+ 'x'.repeat(247),{supports_vision:true,supports_tools:true})]},{id:'disabled',name:'Off',enabled:false,models:[makeModel('all',{supports_vision:true,supports_tools:true})]}]}
const note='11111111-1111-4111-8111-111111111111',book='22222222-2222-4222-8222-222222222222'
const notes={tools:['search_notes','read_note_excerpt'],tool_scope:{note_ids:[note],notebook_ids:[]}}
test('model identity includes provider and preserves original 256-character ID',()=>{
 assert.notEqual(modelKey({provider:'custom',model:'deepseek-flash'}),modelKey({provider:'deepseek',model:'deepseek-flash'}))
 const long=catalog.providers[1].models[1].id;assert.equal(JSON.parse(modelKey({provider:'custom',model:long}))[1],long)
 assert.equal(long.length,256)
 assert.equal(modelReady(catalog,{provider:'custom',model:long},{vision:true}),true)
})
test('capability checks are provider-local and fail closed for disabled/missing/platform off',()=>{
 assert.equal(modelReady(catalog,{provider:'deepseek',model:'deepseek-flash'},{tools:true}),false)
 assert.equal(modelReady(catalog,{provider:'custom',model:'deepseek-flash'},{tools:true}),true)
 assert.equal(modelReady(catalog,{provider:'custom',model:'deepseek-flash'},{vision:true}),false)
 assert.equal(modelReady(catalog,{provider:'disabled',model:'all'}),false)
 assert.equal(modelReady(catalog,{provider:'missing',model:'all'}),false)
 assert.equal(modelReady({...catalog,platform_enabled:false},{provider:'custom',model:'deepseek-flash'}),false)
 assert.equal(modelReady({...catalog,providers:[{id:'custom',name:'No streaming',enabled:true,models:[makeModel('x',{supports_streaming:false})]}]},{provider:'custom',model:'x'}),false)
})
test('visual+tools rejected even if provider advertises both independently',()=>assert.equal(modelReady(catalog,{provider:'custom',model:catalog.providers[1].models[1].id},{vision:true,tools:true}),false))
test('history labels do not depend on current catalog and custom aliases are untouched',()=>{
 assert.equal(modelLabel(undefined,'deepseek-chat'),'deepseek / DeepSeek Chat')
 assert.equal(modelLabel('removed','old/original-model'),'removed / old/original-model')
 assert.equal(modelLabel('custom','deepseek-flash'),'custom / deepseek-flash')
})
test('tools default closed; there is never implicit all-notes permission',()=>{
 assert.doesNotThrow(()=>validateTools({tools:[],tool_scope:null}))
 for(const selection of [{tools:[],tool_scope:{}},{tools:['search_notes'],tool_scope:null},{tools:['search_notes'],tool_scope:{note_ids:[],notebook_ids:[]}}])assert.throws(()=>validateTools(selection))
 assert.doesNotThrow(()=>validateTools(notes))
 assert.doesNotThrow(()=>validateTools({tools:['search_notes'],tool_scope:{note_ids:[],notebook_ids:[book]}}))
})
test('note scope validates IDs, duplicate tool/IDs, note and notebook bounds',()=>{
 assert.throws(()=>validateTools({...notes,tools:['search_notes','search_notes']}))
 assert.throws(()=>validateTools({...notes,tool_scope:{note_ids:[note,note],notebook_ids:[]}}))
 assert.throws(()=>validateTools({...notes,tool_scope:{note_ids:['not-a-uuid'],notebook_ids:[]}}))
 assert.throws(()=>validateTools({...notes,tool_scope:{note_ids:Array(101).fill(note),notebook_ids:[]}}))
 assert.throws(()=>validateTools({...notes,tool_scope:{note_ids:[],notebook_ids:Array(21).fill(book)}}))
})
test('explicit learning dates accept one day and 93 inclusive days; reject impossible, reversed and 94 days',()=>{
 for(const [from,to,valid] of [['2026-01-01','2026-01-01',true],['2026-01-01','2026-04-03',true],['2026-01-01','2026-04-04',false],['2026-02-30','2026-03-01',false],['2026-99-01','2026-99-02',false],['2026-03-02','2026-03-01',false],['','',false]])assert.equal(validLearningDates({date_from:from,date_to:to}),valid)
})
test('learning_summary works alone with empty note IDs, and combined scope is explicit',()=>{
 const learning_summary={date_from:'2026-01-01',date_to:'2026-01-31'}
 assert.doesNotThrow(()=>validateTools({tools:['learning_summary'],tool_scope:{note_ids:[],notebook_ids:[],learning_summary}}))
 assert.doesNotThrow(()=>validateTools({tools:[...notes.tools,'learning_summary'],tool_scope:{...notes.tool_scope,learning_summary}}))
 assert.throws(()=>validateTools({tools:['learning_summary'],tool_scope:{note_ids:[note],notebook_ids:[],learning_summary}}))
 assert.throws(()=>validateTools({...notes,tool_scope:{...notes.tool_scope,learning_summary}}))
})
test('preview requires actual bounded resolved notes and server atomic hash',()=>{
 const preview={tools:notes.tools,scope:{version:1,notes:[{note_id:note,revision:7,title:'Scope'}]},note_count:1,preview_hash:'a'.repeat(64)}
 assert.equal(previewMatches(notes,preview),true)
 assert.equal(previewMatches(notes,{...preview,preview_hash:''}),false)
 assert.equal(previewMatches(notes,{...preview,note_count:101}),false)
 assert.equal(previewMatches(notes,{...preview,scope:{version:1,notes:[]}}),false)
 assert.equal(previewMatches(notes,{...preview,tools:['learning_summary']}),false)
})
test('preview learning source dates must match confirmed request',()=>{
 const learning_summary={date_from:'2026-01-01',date_to:'2026-01-31'},selection={tools:['learning_summary'],tool_scope:{note_ids:[],notebook_ids:[],learning_summary}}
 const preview={tools:selection.tools,scope:{version:1,notes:[],learning_summary:{...learning_summary,kind:'learning_summary',timezone:'Asia/Shanghai',fingerprint:'b'.repeat(64)}},note_count:0,preview_hash:'a'.repeat(64)}
 assert.equal(previewMatches(selection,preview),true)
 assert.equal(previewMatches(selection,{...preview,scope:{...preview.scope,learning_summary:{...preview.scope.learning_summary,date_to:'2026-02-01'}}}),false)
})
