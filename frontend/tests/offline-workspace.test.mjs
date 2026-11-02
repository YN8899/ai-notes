import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import {createRequire} from 'node:module'
import {createHeader,encode,seal} from '../src/features/offline/crypto.ts'
import {OfflineRepository} from '../src/features/offline/repository.ts'
import {emptyWorkspace,unfinished,uuid} from '../src/features/offline/types.ts'
const require=createRequire(new URL('../package.json',import.meta.url)),ts=require('typescript')
const source=fs.readFileSync(new URL('../src/features/offline/OfflineWorkspace.tsx',import.meta.url),'utf8')
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText
const owner='00000000-0000-4000-8000-000000000001',noteId='00000000-0000-4000-8000-000000000002',epoch='00000000-0000-4000-8000-000000000003'
const epochError='服务器同步代次已改变，已停止全部同步并保留本机修改'
const noop=()=>null,jsx=(type,props,key)=>({type,props,key}),tick=()=>new Promise(resolve=>setImmediate(resolve))
let key
const initialized=createHeader(owner,'component-handler-local-passphrase').then(value=>key=value.key)
async function mount({refreshFails=false,revoke=false}={}){
 await initialized
 let live=true,reads=0,slots=[],index=0,effects=[],tree
 const cap={owner,key,generation:1,signal:new AbortController().signal,check(){if(!live)throw new DOMException('vault retired','AbortError')}}
 const initial=emptyWorkspace();initial.syncEpoch=epoch;initial.notes.push({id:noteId,revision:1,title:'pending local note',kind:'text',notebook_id:null,tag_ids:[],body_json:{},plain_text:'preserved local body',deleted_at:null});initial.queue.push({operation:{operation_id:uuid(),resource_id:noteId,sync_epoch:epoch,kind:'note.create',base_revision:null,payload:{title:'pending local note'}},depends:[],base:{},state:'pending',sent:false})
 let row={...await seal(key,owner,'records','workspace',encode(initial)),owner,id:`${owner}:workspace`,version:1}
 const db={read:async()=>{cap.check();if(refreshFails&&++reads>1)throw new Error('secondary encrypted refresh unavailable');return structuredClone(row)},put:async(_cap,expected,rows)=>{cap.check();assert.equal(row.version,expected);row=structuredClone(rows.find(value=>value.id===`${owner}:workspace`))}}
 const commandRepo=new OfflineRepository(cap,{...db,read:async()=>structuredClone(row)})
 const depsEqual=(a,b)=>a&&b&&a.length===b.length&&a.every((value,i)=>value===b[i])
 const react={useState(initial){const n=index++;if(!(n in slots))slots[n]=typeof initial==='function'?initial():initial;return [slots[n],value=>{slots[n]=typeof value==='function'?value(slots[n]):value}]},useMemo(fn,deps){const n=index++;if(!slots[n]||!depsEqual(slots[n].deps,deps))slots[n]={deps,value:fn()};return slots[n].value},useCallback(fn,deps){return react.useMemo(()=>fn,deps)},useEffect(fn,deps){const n=index++;if(!depsEqual(slots[n],deps)){slots[n]=deps;effects.push(fn)}}}
 // Test the actual workspace handler. The injected domain command commits through a real
 // encrypted repository, then fails; it does not claim to test HTTP or the server epoch.
 const command=async()=>{await commandRepo.mutate(value=>{value.syncBlocked=epochError;value.queue[0].state='sending';value.queue[0].sent=true;value.queue[0].error=epochError});if(revoke)live=false;throw new Error(epochError)}
 class WorkspaceRepository extends OfflineRepository{constructor(cap){super(cap,db)}}
 const OfflineNote=()=>null,out={exports:{}}
 const resolver=id=>id==='react'?react:id==='react/jsx-runtime'?{jsx,jsxs:jsx,Fragment:'Fragment'}:id==='./types.ts'?{unfinished,uuid}:id==='./repository.ts'?{OfflineRepository:WorkspaceRepository}:id==='./sync.ts'?{syncQueue:command}:id==='./OfflineNote.tsx'?{__esModule:true,default:OfflineNote}:id==='./OfflineAssets.tsx'?{OfflinePdfPicker:noop}:id==='./vault.ts'?{clearVault:noop,lockVault:noop,revokeVaultUrl:noop,vaultUrl:noop}:id==='./queue.ts'?{copyNote:noop,edit:noop}:id.endsWith('.css')?{}:{__esModule:true,default:noop}
 vm.runInNewContext(code,{...out,require:resolver,console,Error,DOMException,window:{addEventListener:noop,removeEventListener:noop,confirm:()=>true},Date},{filename:'OfflineWorkspace.tsx'})
 function render(){index=0;tree=out.exports.default({cap,onClose:noop});const active=effects;effects=[];active.forEach(fn=>fn());return tree}
 function elements(node,result=[]){if(Array.isArray(node))node.forEach(value=>elements(value,result));else if(node&&typeof node==='object'){result.push(node);elements(node.props?.children,result)}return result}
 const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):node?.props?text(node.props.children):''
 const button=label=>elements(tree).find(node=>node.type==='button'&&text(node)===label)
 render();for(let n=0;n<100;n++){await new Promise(resolve=>setTimeout(resolve,2));render();if(elements(tree).some(node=>node.type==='button'&&node.props.className==='note-open'))break}
 return {render,button,elements:()=>elements(tree),text:()=>text(tree),repo:commandRepo,async selectNote(){elements(tree).find(node=>node.type==='button'&&node.props.className==='note-open').props.onClick();render()},note:()=>elements(tree).find(node=>node.type===OfflineNote),async sync(){button('验证服务器账号并同步').props.onClick();for(let n=0;n<100;n++){await new Promise(resolve=>setTimeout(resolve,2));render();if(!elements(tree).find(node=>node.type==='fieldset'&&node.props.className==='offline-editing')?.props.disabled)return}throw new Error('workspace handler did not settle')}}
}
test('actual workspace handler rereads committed encrypted epoch block after sync throws and retains pending local content',async()=>{
 const ui=await mount();await ui.selectNote();const before=ui.note();assert.equal(ui.button('验证服务器账号并同步').props.disabled,false)
 await ui.sync();assert.equal(ui.button('验证服务器账号并同步').props.disabled,true);assert.match(ui.text(),/服务器同步代次已改变/);assert.match(ui.text(),/已发送，重试沿用原编号/)
 assert.equal(ui.note().key,before.key);assert.equal(ui.note().props.note.plain_text,'preserved local body');assert.equal((await ui.repo.value()).queue.length,1)
})
test('secondary refresh failure preserves the original sync error, old projection and dirty editor identity',async()=>{
 const ui=await mount({refreshFails:true});await ui.selectNote();const before=ui.note();before.props.onDirty(true);ui.render()
 await ui.sync();assert.match(ui.text(),/服务器同步代次已改变/);assert.doesNotMatch(ui.text(),/secondary encrypted refresh unavailable/)
 assert.equal(ui.note().key,before.key);assert.equal(ui.note().props.note.plain_text,'preserved local body');assert.equal(ui.button('验证服务器账号并同步').props.disabled,true)
})
test('revoked vault cannot publish the late operation error or encrypted reread result',async()=>{
 const ui=await mount({revoke:true});await ui.sync();assert.doesNotMatch(ui.text(),/服务器同步代次已改变|vault retired/)
})
