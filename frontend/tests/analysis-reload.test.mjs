import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import {createRequire} from 'node:module'
import {createSessionState} from '../src/session.ts'
import {restoreSubmission,submissionStorageKey} from '../src/features/personalization/analysis-submission.ts'
const require=createRequire(new URL('../package.json',import.meta.url)),ts=require('typescript')
const noop=()=>null,jsx=(type,props)=>({type,props})
class ApiError extends Error{constructor(status){super('synthetic '+status);this.status=status;this.code='synthetic'}}
const summary={source:{date_from:'2026-11-01',date_to:'2026-11-01',timezone:'Asia/Shanghai'},facts:{},sample:{insufficient:true}}
const preferences={revision:1,ai_analysis_enabled:true}
const preview={id:'11111111-1111-4111-8111-111111111111',preview_hash:'a'.repeat(64),summary,provider:'deepseek',model:'deepseek-flash',input_messages:[{role:'user',content:'synthetic facts'}],input_bytes:24,expires_at:'2099-01-01T00:00:00Z',planned_calls:1,tools:[]}
const catalog={platform_enabled:true,providers:[{id:'deepseek',enabled:true,models:[{id:'deepseek-flash',supports_text:true,supports_streaming:true}]}]}
function documentSession(owner='owner'){const session=createSessionState();session.verify(owner,session.ticket());return session}
function environment(){return {scope:'nonsecret-persisted-scope',storage:new Map(),readFailure:false,writeFailure:false,scopeFailure:false}}
function storageFor(env){return {getItem:key=>{if(env.readFailure)throw new Error('read denied');return env.storage.get(key)??null},setItem:(key,value)=>{if(env.writeFailure)throw new Error('write denied');env.storage.set(key,value)},removeItem:key=>env.storage.delete(key)}}
function mount(env,session=documentSession(),transport=async()=>{throw new ApiError(408)}){
 let slots=[],index=0,effects=[],tree,uuids=0
 const calls=[],queries=[],props={owner:session.snapshot().owner,sessionId:session.snapshot().id,preferences,onPreferences:noop}
 const react={useState(v){const n=index++;if(!(n in slots))slots[n]=typeof v==='function'?v():v;return [slots[n],value=>slots[n]=typeof value==='function'?value(slots[n]):value]},useRef(v){const n=index++;return slots[n]??(slots[n]={current:v})},useEffect(fn,deps){const n=index++;if(!slots[n]||deps.some((v,i)=>v!==slots[n][i])){slots[n]=deps;effects.push(fn)}},lazy:()=>noop,Suspense:'Suspense'}
 const cache={}
 function load(relative){
  if(cache[relative])return cache[relative]
  const source=fs.readFileSync(new URL('../src/'+relative,import.meta.url),'utf8'),out={exports:{}};cache[relative]=out.exports
  const query=opts=>{queries.push(opts);return {data:opts.queryKey.includes('facts')?{summary,rules:[]}:opts.queryKey[0]==='ai-catalog'?catalog:opts.queryKey.includes('analysis-history')?[]:undefined,isPending:false,isFetching:false,error:null,refetch:async()=>{calls.push(['read-only-refresh','GET'])}}}
  const api=async(...args)=>{calls.push(args);if(args[0].endsWith('/preview'))return preview;return transport(...args)}
  const resolver=id=>id==='react'?react:id==='react/jsx-runtime'?{jsx,jsxs:jsx,Fragment:'Fragment'}:id==='@tanstack/react-query'?{useQuery:query,useQueryClient:()=>({setQueryData:noop,invalidateQueries:noop})}:id==='../../api'?{ApiError,date:String,api}:id==='../../uuid'?{createUuid:()=>`00000000-0000-4000-8000-${String(++uuids).padStart(12,'0')}`}:id==='../../session'?{authSession:session,readSession:()=>{if(env.scopeFailure)throw new Error('identity read denied');return env.scope===null?null:{scope:env.scope}}}:id==='./personalization'?load('features/personalization/personalization.ts'):id==='./types.ts'?{sectionLabels:{home:'Home'}}:id==='./analysis-submission'?load('features/personalization/analysis-submission.ts'):id==='../ai/model-catalog'?load('features/ai/model-catalog.ts'):id==='../learning/learning'?{rangeQuery:()=>''}:new Proxy({},{get:()=>noop})
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText,{...out,require:resolver,Date,TextEncoder,window:{sessionStorage:storageFor(env),setInterval:()=>1,clearInterval:noop},console},{filename:relative})
  return cache[relative]=out.exports
 }
 const Component=load('features/personalization/AnalysisPanel.tsx').AnalysisPanel
 const render=()=>{index=0;tree=Component(props);const active=effects;effects=[];active.forEach(f=>f());return tree}
 const elements=(node,result=[])=>{if(Array.isArray(node))node.forEach(n=>elements(n,result));else if(node&&typeof node==='object'){result.push(node);elements(node.props?.children,result)}return result}
 const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):node?.props?text(node.props.children):''
 const button=label=>elements(tree).find(n=>n.type==='button'&&text(n).includes(label))
 return {render,button,calls,queries,session,props,text:()=>text(tree),async click(label){const b=button(label);assert.ok(b,label);assert.notEqual(b.props.disabled,true,label+' must be enabled');b.props.onClick();await new Promise(resolve=>setImmediate(resolve));render()},async prepare(){render();await this.click('预览一次');elements(tree).find(n=>n.type==='input'&&n.props.type==='checkbox').props.onChange({target:{checked:true}});render()},submit(){return this.click('确认创建一次')}}
}
test('actual AnalysisPanel HTTP408 survives a real new-document session ID with the same persistent scope; reload and history never POST, explicit retry keeps the exact key',async()=>{
 const env=environment(),firstSession=documentSession(),first=mount(env,firstSession);await first.prepare();await first.submit()
 const original=first.calls.find(c=>c[0]==='/personalization/analysis')[2]
 assert.ok(first.button('使用同一请求标识'));assert.equal(env.storage.size,1)
 const secondSession=documentSession();assert.notEqual(firstSession.snapshot().id,secondSession.snapshot().id)
 const restored=mount(env,secondSession);restored.render();assert.ok(restored.button('使用同一请求标识'));assert.equal(restored.button('预览一次').props.disabled,true);assert.equal(restored.calls.length,0)
 await restored.click('读取历史核对状态');assert.equal(restored.calls.filter(c=>c[1]==='POST').length,0)
 await restored.click('使用同一请求标识');const retried=restored.calls.find(c=>c[0]==='/personalization/analysis')[2]
 assert.equal(JSON.stringify(retried),JSON.stringify(original));assert.equal(restored.calls.filter(c=>c[1]==='POST').length,1)
})
test('new login scope and other owner cannot recover or overwrite a previous session pending key',async()=>{
 const env=environment(),first=mount(env);await first.prepare();await first.submit();const original=[...env.storage.entries()]
 const other=mount(env,documentSession('other-owner'));other.render();assert.equal(other.button('使用同一请求标识'),undefined);assert.equal(other.button('预览一次').props.disabled,false)
 env.scope='fresh-login-scope';const newLogin=mount(env);newLogin.render();assert.equal(newLogin.button('使用同一请求标识'),undefined);assert.equal(newLogin.button('预览一次').props.disabled,false)
 assert.deepEqual([...env.storage.entries()],original)
})
test('late HTTP408 from the retired document cannot write error/pending into the replacement login or overwrite its stored request',async()=>{
 const env=environment();let rejectOriginal
 const oldSession=documentSession(),old=mount(env,oldSession,()=>new Promise((_resolve,reject)=>rejectOriginal=reject));await old.prepare()
 old.button('确认创建一次').props.onClick();await new Promise(resolve=>setImmediate(resolve))
 oldSession.invalidate(oldSession.ticket());env.scope='replacement-login'
 const replacement=mount(env);await replacement.prepare();await replacement.submit();const saved=[...env.storage.entries()]
 rejectOriginal(new ApiError(408));await new Promise(resolve=>setImmediate(resolve));old.render();replacement.render()
 assert.deepEqual([...env.storage.entries()],saved);assert.ok(replacement.button('使用同一请求标识'));assert.doesNotMatch(old.text(),/synthetic 408/)
})
test('missing/unreadable scope or storage fails closed for model submission while facts and history remain available',async()=>{
 for(const failure of ['scopeFailure','readFailure','missingScope']){
  const env=environment();if(failure==='missingScope')env.scope=null;else env[failure]=true
  const ui=mount(env);ui.render();assert.equal(ui.button('预览一次').props.disabled,true);assert.match(ui.text(),/已禁止新的模型提交/)
  assert.ok(ui.queries.some(q=>q.queryKey.includes('facts')));assert.ok(ui.queries.some(q=>q.queryKey.includes('analysis-history')))
  await ui.click('只刷新历史');assert.equal(ui.calls.filter(c=>c[1]==='POST').length,0)
 }
})
test('write failure after a confirmed preview sends no model POST and revokes further creation',async()=>{
 const env=environment(),ui=mount(env);await ui.prepare();env.writeFailure=true;await ui.submit()
 assert.equal(ui.calls.filter(c=>c[0]==='/personalization/analysis').length,0);assert.equal(ui.button('预览一次').props.disabled,true);assert.match(ui.text(),/已禁止新的模型提交/)
})
test('restoration rejects missing identity and corrupt pending records instead of sharing an empty scope or silently creating a new request',()=>{
 const env=environment(),storage=storageFor(env),body={preview_id:preview.id,preview_hash:preview.preview_hash,request_key:'22222222-2222-4222-8222-222222222222'}
 assert.throws(()=>submissionStorageKey('owner',''));assert.equal(restoreSubmission('owner',()=>undefined,()=>storage).key,null)
 env.storage.set(submissionStorageKey('owner',env.scope),JSON.stringify(body));assert.deepEqual(restoreSubmission('owner',()=>env.scope,()=>storage).pending,body)
 env.storage.set(submissionStorageKey('owner',env.scope),'invalid');assert.equal(restoreSubmission('owner',()=>env.scope,()=>storage).key,null)
})
