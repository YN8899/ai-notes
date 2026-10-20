import assert from 'node:assert/strict'
import {test} from 'node:test'
import {api,ApiError} from '../src/api.ts'
import {authSession,SessionChangedError} from '../src/session.ts'

const deferred=()=>{let resolve;const promise=new Promise(yes=>resolve=yes);return{promise,resolve}}
const flush=async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve()}
const login=owner=>{authSession.invalidate(authSession.ticket(),{broadcast:false});return authSession.login(owner,authSession.ticket())}
const response=(status,json)=>({status,ok:status<400,json:async()=>json})

test('a mutation waiting for CSRF cannot submit old content using a later login cookie',async()=>{
  const original=globalThis.fetch,csrf=deferred(),calls=[];login('owner-a')
  try{globalThis.fetch=(url,options)=>{calls.push({url,options});return csrf.promise};const work=api('/notes','POST',{title:'owner-a private draft'});const rejected=assert.rejects(work,SessionChangedError);login('owner-b');assert.equal(calls[0].options.signal.aborted,true);csrf.resolve(response(200,{csrf_token:'old-token'}));await rejected;assert.equal(calls.length,1);assert.equal(authSession.snapshot().owner,'owner-b')}finally{globalThis.fetch=original}
})
test('late JSON content from an old session is discarded even if transport ignores abort',async()=>{
  const original=globalThis.fetch,body=deferred();login('owner-a')
  try{globalThis.fetch=async()=>({status:200,ok:true,json:()=>body.promise});const work=api('/notes/private');const rejected=assert.rejects(work,SessionChangedError);await flush();login('owner-b');body.resolve({plain_text:'old private body'});await rejected;assert.equal(authSession.snapshot().owner,'owner-b')}finally{globalThis.fetch=original}
})
test('an old response 401 cannot invalidate a new account session',async()=>{
  const original=globalThis.fetch,headers=deferred();login('owner-a')
  try{globalThis.fetch=()=>headers.promise;const work=api('/auth/me');const rejected=assert.rejects(work,SessionChangedError);const next=login('owner-b');headers.resolve(response(401,{code:'unauthenticated'}));await rejected;assert.equal(authSession.snapshot(),next)}finally{globalThis.fetch=original}
})
test('invalid public login credentials do not invalidate or broadcast an anonymous session',async()=>{
  const original=globalThis.fetch;authSession.invalidate(authSession.ticket(),{broadcast:false});const before=authSession.ticket()
  try{globalThis.fetch=async url=>response(url.endsWith('/csrf')?200:401,url.endsWith('/csrf')?{csrf_token:'token'}:{code:'credentials_invalid',message:'invalid',request_id:'test'});await assert.rejects(api('/auth/login','POST',{username:'synthetic',password:'synthetic'}),error=>error instanceof ApiError&&error.status===401);assert.equal(authSession.snapshot(),before)}finally{globalThis.fetch=original}
})
test('a current private 401 locks the session before any caller can reuse its content',async()=>{
  const original=globalThis.fetch;login('owner-a')
  try{globalThis.fetch=async()=>response(401,{code:'unauthenticated',message:'login required'});await assert.rejects(api('/notes/private'),SessionChangedError);assert.equal(authSession.snapshot().phase,'anonymous')}finally{globalThis.fetch=original}
})
test('the logout request is allowed to finish inside its own closing barrier',async()=>{
  const original=globalThis.fetch,calls=[];login('owner-a');const closing=authSession.beginLogout(authSession.ticket())
  try{globalThis.fetch=async url=>{calls.push(url);return response(url.endsWith('/csrf')?200:204,{csrf_token:'token'})};await api('/auth/logout','POST',{});assert.equal(calls.length,2);assert.equal(authSession.current(closing),true);authSession.invalidate(closing);assert.equal(authSession.snapshot().phase,'anonymous')}finally{globalThis.fetch=original}
})
