import assert from 'node:assert/strict'
import {test} from 'node:test'
import {apiResponse,logoutTab} from '../src/api.ts'
import {sendContent} from '../src/features/files/upload.ts'
import {clearSession,initializeTabSession,readSession,saveSession,sessionEndedEvent} from '../src/session.ts'

const values=new Map(),events=new EventTarget()
let afterRead
const storage={getItem(key){const value=values.get(key)??null;const run=afterRead;afterRead=undefined;run?.();return value},setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)}
globalThis.window={sessionStorage:storage,location:{origin:'http://testserver'},dispatchEvent:events.dispatchEvent.bind(events)}
globalThis.sessionStorage=storage
globalThis.BroadcastChannel=undefined
Object.defineProperty(globalThis,'performance',{value:{timeOrigin:100,getEntriesByType:()=>[{type:'reload'}]},configurable:true})
const alice={session_token:'a'.repeat(43),csrf_token:'c'.repeat(43)}
const bob={session_token:'b'.repeat(43),csrf_token:'d'.repeat(43)}
const expired=()=>new Response(JSON.stringify({code:'login_required',message:'expired',request_id:'qa'}),{status:401,headers:{'Content-Type':'application/json'}})

test('a 401 uses the same credential snapshot as the request headers',async()=>{
  await initializeTabSession();saveSession(alice)
  afterRead=()=>queueMicrotask(()=>saveSession(bob))
  let sent
  globalThis.fetch=async(_url,init)=>{sent=init.headers.get('Authorization');return expired()}
  assert.equal((await apiResponse('/auth/me')).status,401)
  assert.equal(sent,'Bearer '+alice.session_token)
  assert.equal(readSession().session_token,bob.session_token,'Alice response must not clear Bob')
})

test('a late logout response cannot sign out a replacement login',async()=>{
  saveSession(alice)
  let finish,sent
  const sending=new Promise(resolve=>{sent=resolve})
  globalThis.fetch=async()=>{sent();return new Promise(resolve=>{finish=resolve})}
  const logout=logoutTab();await sending
  saveSession(bob)
  finish(new Response('{"ok":true}',{headers:{'Content-Type':'application/json'}}))
  clearSession(await logout)
  assert.equal(readSession().session_token,bob.session_token)
})

test('upload headers and 401 revocation share one immutable session snapshot',async()=>{
  saveSession(alice)
  afterRead=()=>queueMicrotask(()=>saveSession(bob))
  let sent
  globalThis.XMLHttpRequest=class {
    upload={};headers={};status=401;responseText=JSON.stringify({code:'login_required',message:'expired'})
    open(){} setRequestHeader(key,value){this.headers[key]=value}
    send(){sent=this.headers.Authorization;this.onload();this.onloadend()}
  }
  await assert.rejects(sendContent({id:'upload-qa',upload_url:'/api/v1/uploads/upload-qa/content',upload_method:'PUT'},new File(['x'],'test.png',{type:'image/png'}),()=>{},new AbortController().signal),/expired/)
  assert.equal(sent,'Bearer '+alice.session_token)
  assert.equal(readSession().session_token,bob.session_token)
})

test('a delayed ownership reply still clears a copied reload session after the initial handshake',async()=>{
  saveSession(alice)
  let ended
  const end=new Promise(resolve=>{ended=resolve})
  events.addEventListener(sessionEndedEvent,ended,{once:true})
  globalThis.BroadcastChannel=class {
    postMessage(message){if(message.type==='claim')setTimeout(()=>this.onmessage({data:{type:'claimed',scope:message.scope,target:message.claim,started:0,claim:'older-document'}}),300)}
  }
  const copy=await import('../src/session.ts?delayed-copy')
  await copy.initializeTabSession()
  assert.ok(copy.readSession(),'reload is retained while awaiting other documents')
  await end
  assert.equal(copy.readSession(),null,'late replies must not be ignored after 150ms')
  globalThis.BroadcastChannel=undefined
})
