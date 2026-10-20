import assert from 'node:assert/strict'
import { test } from 'node:test'
import { authSession } from '../src/session.ts'
import { sendContent } from '../src/features/files/upload.ts'

const upload={id:'synthetic-upload',upload_url:'/api/v1/uploads/synthetic-upload/content',upload_method:'PUT'}
const file=new File(['synthetic'],'image.png',{type:'image/png'})
function environment(){
  const before={window:globalThis.window,fetch:globalThis.fetch,xhr:globalThis.XMLHttpRequest},requests=[]
  globalThis.window={location:{origin:'http://localhost:5173'}}
  class XHR {
    constructor(){this.upload={};this.aborted=false;requests.push(this)}
    open(){} setRequestHeader(){} send(){this.sent=true}
    abort(){this.aborted=true;this.onabort?.()}
  }
  globalThis.XMLHttpRequest=XHR
  authSession.login('synthetic-upload-owner',authSession.ticket())
  return{requests,restore(){globalThis.window=before.window;globalThis.fetch=before.fetch;globalThis.XMLHttpRequest=before.xhr}}
}

test('upload waiting for CSRF cannot start transport under a later account cookie',async()=>{
  const env=environment();let resolve
  globalThis.fetch=()=>new Promise(yes=>{resolve=yes})
  try{
    const pending=sendContent(upload,file,()=>assert.fail('no progress expected'),new AbortController().signal)
    authSession.invalidate(authSession.ticket());authSession.login('new-upload-owner',authSession.ticket())
    resolve(new Response(JSON.stringify({csrf_token:'synthetic-csrf'})))
    await assert.rejects(pending,{name:'AbortError'});assert.equal(env.requests.length,0)
  }finally{env.restore()}
})

test('session invalidation aborts an in-flight upload and ignores late progress or result',async()=>{
  const env=environment(),progress=[]
  globalThis.fetch=async()=>new Response(JSON.stringify({csrf_token:'synthetic-csrf'}))
  try{
    const pending=sendContent(upload,file,value=>progress.push(value),new AbortController().signal)
    for(let i=0;i<20&&!env.requests.length;i++)await new Promise(resolve=>setImmediate(resolve))
    assert.equal(env.requests.length,1);const request=env.requests[0];assert.equal(request.sent,true)
    authSession.invalidate(authSession.ticket())
    await assert.rejects(pending,{name:'AbortError'});assert.equal(request.aborted,true)
    request.upload.onprogress({lengthComputable:true,loaded:10,total:10})
    request.status=200;request.responseText=JSON.stringify(upload);request.onload()
    assert.deepEqual(progress,[])
  }finally{env.restore()}
})
