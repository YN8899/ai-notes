import fs from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'
import test from 'node:test'
import {createRequire} from 'node:module'
const require=createRequire(new URL('../package.json',import.meta.url)),ts=require('typescript')
const source=fs.readFileSync(new URL('../src/features/ai/AiChat.tsx',import.meta.url),'utf8')
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText
class ApiError extends Error{constructor(status){super('synthetic '+status);this.status=status;this.code='synthetic'}}
const no=()=>null
/** Real AiChat request/error handlers, with only rendering/query/transport replaced. */
async function retryBodies(status,changePrompt=false){
 let slots=[],index=0,effects=[],calls=[],last,uuid=0,failure=true
 const react={useRef(value){const n=index++;return slots[n]??(slots[n]={current:value})},useState(value){const n=index++;if(!(n in slots))slots[n]=typeof value==='function'?value():value;return [slots[n],v=>slots[n]=typeof v==='function'?v(slots[n]):v]},useEffect(fn,deps){const n=index++;if(!slots[n]||deps.some((v,i)=>v!==slots[n][i])){slots[n]=deps;effects.push(fn)}},useLayoutEffect(fn,deps){this.useEffect(fn,deps)}}
 react.useLayoutEffect=react.useEffect
 const catalog={platform_enabled:true,providers:[{id:'deepseek',enabled:true,models:[{id:'deepseek-flash',supports_text:true,supports_streaming:true}]}]}
 const api=async(p,m,b)=>{calls.push({p,m,b});if(failure){failure=false;throw status==='network'?new Error('network'):new ApiError(status)}return {id:'run',conversation_id:'conv',prompt:b.prompt,answer:'',status:'queued'}}
 const query={useQueryClient:()=>({setQueryData:no,invalidateQueries:no}),useQuery({queryKey}){return {data:queryKey[0]==='ai-catalog'?catalog:queryKey[0]==='ai-conversation'?{id:'conv',title:'Test',runs:[]}:['ai-conversations','ai-saved-notes'].includes(queryKey[0])?[]:undefined,isPending:false,isFetching:false,error:null,refetch:no}},useMutation(config){return {isPending:false,isError:false,error:null,mutate(arg){last=(async()=>{try{const out=await config.mutationFn(arg);config.onSuccess?.(out,arg)}catch(e){config.onError?.(e)}finally{config.onSettled?.()}})()}}}}
 const jsx=(type,props)=>({type,props})
 const requireMock=id=>id==='react'?react:id==='react/jsx-runtime'?{jsx,jsxs:jsx,Fragment:'fragment'}:id==='@tanstack/react-query'?query:id==='../../api'?{api,ApiError,date:String}:id==='../../uuid'?{createUuid:()=>`key-${++uuid}`}:id==='../../session'?{authSession:{ticket:()=>({id:'s',owner:'owner'}),current:()=>true}}:id==='./model-catalog'?{defaultModel:{provider:'deepseek',model:'deepseek-flash'},modelReady:()=>true,modelLabel:no}:id==='./ToolScopePicker'?{emptyToolDraft:{selection:{tools:[],tool_scope:null},preview:null,confirmed:false,contextKey:''},ToolScopePicker:no}:id==='./chat-stream'?{active:r=>r?.status==='queued',readStream:()=>Promise.resolve()}:new Proxy({emptyChoice:{noteId:''},emptyImageChoice:{noteId:'',pageId:''},buildPdfInput:no,pdfReady:()=>false},{get:(t,p)=>t[p]||no})
 const box={exports:{},require:requireMock,sessionStorage:{getItem:()=> 'conv',setItem:no},queueMicrotask,AbortController,console}
 vm.runInNewContext(code,box,{filename:'AiChat.formal.js'})
 const render=()=>{index=0;const tree=box.exports.default({owner:'owner'});const active=effects;effects=[];for(const f of active)f();return tree}
 const find=(tree,type)=>{if(Array.isArray(tree)){for(const x of tree){const f=find(x,type);if(f)return f}}else if(tree&&typeof tree==='object'){if(tree.type===type)return tree;return find(tree.props?.children,type)}}
 let tree=render();find(tree,'textarea').props.onChange({target:{value:'same prompt'}});tree=render();find(tree,'form').props.onSubmit({preventDefault:no});await last
 assert.equal(calls.filter(x=>x.m==='POST').length,1,'error handling must not automatically retry')
 tree=render();if(changePrompt){find(tree,'textarea').props.onChange({target:{value:'changed prompt'}});tree=render()}
 find(tree,'form').props.onSubmit({preventDefault:no});await last
 return calls.filter(x=>x.m==='POST').map(x=>x.b)
}
test('actual AiChat network/408/500/503 unknown results retain original key on explicit identical retry',async()=>{
 for(const status of ['network',408,500,503]){const bodies=await retryBodies(status);assert.equal(bodies.length,2);assert.equal(bodies[0].request_key,bodies[1].request_key,String(status))}
})
test('actual AiChat definitive client rejections or edited inputs create a fresh key only on explicit submit',async()=>{
 for(const status of [400,403,409,422]){const bodies=await retryBodies(status);assert.notEqual(bodies[0].request_key,bodies[1].request_key,String(status))}
 const edited=await retryBodies(408,true);assert.notEqual(edited[0].request_key,edited[1].request_key);assert.equal(edited[1].prompt,'changed prompt')
})
