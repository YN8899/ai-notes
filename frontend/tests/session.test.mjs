import assert from 'node:assert/strict'
import {test} from 'node:test'
import {createSessionState,receiveSessionEvent,authSession,startSessionSync,SessionChangedError} from '../src/session.ts'

const initial={id:'shared-cookie-generation',previous:'before',phase:'active',owner:'owner-a'}
const active=publish=>{const session=createSessionState(publish,initial);session.verify('owner-a',session.ticket());return session}

test('logout synchronously revokes the old owner in both tabs before React can unmount',()=>{
  const events=[],a=active(event=>events.push(event)),b=active()
  const old=b.ticket();a.beginLogout(a.ticket());b.receive(events[0])
  assert.equal(b.snapshot().phase,'closing')
  assert.equal(b.current(old,'owner-a'),false)
  assert.throws(()=>b.login('owner-b',b.ticket()),SessionChangedError)
})
test('a relogin by the same owner rejects old logout completion, old active markers and old me results',()=>{
  const events=[],session=active(event=>events.push(event)),before=session.ticket()
  const closing=session.beginLogout(before);session.invalidate(closing)
  const next=session.login('owner-a',session.ticket())
  assert.equal(session.invalidate(closing),false)
  session.receive(events[0],true);session.receive(initial,true)
  assert.equal(session.snapshot(),next)
  assert.equal(session.current(before,'owner-a'),false)
  assert.throws(()=>session.verify('owner-a',before),SessionChangedError)
})
test('an account-changing broadcast locks old content until the shared cookie is verified',()=>{
  const a=active(),old=a.ticket()
  a.receive({id:'new-cookie',previous:initial.id,owner:'owner-b',phase:'active'})
  assert.equal(a.snapshot().phase,'checking')
  assert.equal(a.current(a.ticket(),'owner-b'),false)
  assert.equal(a.current(old,'owner-a'),false)
  a.verify('owner-b',a.ticket())
  assert.equal(a.current(a.ticket(),'owner-b'),true)
})
test('a failed storage write does not suppress the channel notification behind a stale marker',()=>{
  const events=[],sender=active(event=>events.push(event)),receiver=active()
  sender.beginLogout(sender.ticket())
  receiveSessionEvent(receiver,events[0],initial,false)
  assert.equal(receiver.snapshot().phase,'closing')
  sender.invalidate(sender.ticket());receiveSessionEvent(receiver,events[1],initial,false)
  assert.equal(receiver.snapshot().phase,'anonymous')
})
test('unverified stale active messages cannot relabel and clear the newly authenticated owner',()=>{
  const session=createSessionState(),current=session.login('owner-b',session.ticket())
  session.receive({id:'unseen-old-active',previous:'old-anonymous',owner:'owner-a',phase:'active'})
  assert.equal(session.snapshot().phase,'checking')
  assert.equal(session.snapshot().owner,'owner-b')
  session.verify('owner-b',session.ticket())
  assert.equal(session.snapshot().owner,'owner-b')
  assert.equal(session.current(current,'owner-b'),false)
})
test('rejecting a stale different-owner active preserves the verified wire ID used by a peer logout',()=>{
  const outgoing=[],session=createSessionState(event=>outgoing.push(event)),verified=session.login('owner-b',session.ticket())
  const peer=createSessionState(()=>{},{id:verified.id,previous:'before',owner:'owner-b',phase:'active'});peer.verify('owner-b',peer.ticket())
  session.receive({id:'unseen-owner-a-wire',previous:'old-owner-a',owner:'owner-a',phase:'active'})
  assert.equal(session.snapshot().owner,'owner-b');session.verify('owner-b',session.ticket())
  assert.equal(session.snapshot().id,verified.id)
  session.beginLogout(session.ticket());receiveSessionEvent(peer,outgoing.at(-1),null,false);assert.equal(peer.snapshot().phase,'closing')
})
test('a new explicit login discards an older unverified active candidate',()=>{
  const session=createSessionState();session.login('owner-b',session.ticket())
  session.receive({id:'old-owner-a-candidate',previous:'old-a',owner:'owner-a',phase:'active'})
  session.invalidate(session.ticket());const next=session.login('owner-b',session.ticket());session.verify('owner-b',next)
  assert.equal(session.snapshot().id,next.id)
})
test('simultaneous channel-only bootstraps converge and neither tab misses logout',()=>{
  const aEvents=[],bEvents=[],a=createSessionState(event=>aEvents.push(event)),b=createSessionState(event=>bEvents.push(event))
  a.verify('owner-a',a.ticket());b.verify('owner-a',b.ticket())
  const firstA=aEvents[0],firstB=bEvents[0]
  receiveSessionEvent(a,firstB,null,false);receiveSessionEvent(b,firstA,null,false)
  for(const event of aEvents.slice(1))receiveSessionEvent(b,event,null,false)
  for(const event of bEvents.slice(1))receiveSessionEvent(a,event,null,false)
  if(a.snapshot().phase==='checking')a.verify('owner-a',a.ticket())
  if(b.snapshot().phase==='checking')b.verify('owner-a',b.ticket())
  assert.equal(a.snapshot().id,b.snapshot().id)
  const closing=a.beginLogout(a.ticket());receiveSessionEvent(b,aEvents.at(-1),null,false)
  assert.equal(b.snapshot().phase,'closing');a.invalidate(closing);receiveSessionEvent(b,aEvents.at(-1),null,false);assert.equal(b.snapshot().phase,'anonymous')
})
test('a delayed bootstrap reply among three channel-only tabs cannot reverse convergence',()=>{
  const marker={phase:'active',owner:null,previous:'before'},ae=[],be=[],ce=[]
  const a=createSessionState(event=>ae.push(event),{...marker,id:'a'}),b=createSessionState(event=>be.push(event),{...marker,id:'b'}),c=createSessionState(event=>ce.push(event),{...marker,id:'c'})
  for(const session of [a,b,c])session.verify('owner-a',session.ticket())
  const receive=(session,event)=>receiveSessionEvent(session,event,null,false)
  receive(a,be[0]);receive(b,ce[0]);receive(b,ae[0]);receive(c,be[1]);receive(c,ae[0]);receive(a,be[1])
  for(const session of [a,b,c])if(session.snapshot().phase==='checking')session.verify('owner-a',session.ticket())
  assert.deepEqual([a,b,c].map(session=>session.snapshot().id),['a','a','a'])
  a.beginLogout(a.ticket());receive(b,ae.at(-1));receive(c,ae.at(-1));assert.equal(b.snapshot().phase,'closing');assert.equal(c.snapshot().phase,'closing')
})
test('a late bootstrap cannot cancel logout and receives the existing bounded closing barrier',()=>{
  const events=[],session=active(event=>events.push(event)),closing=session.beginLogout(session.ticket())
  const peer=createSessionState(()=>{},{id:'late-bootstrap-peer',previous:'before',owner:null,phase:'active'})
  session.receive({id:peer.snapshot().id,previous:peer.snapshot().id,owner:'owner-a',phase:'active',bootstrap:true})
  assert.equal(session.snapshot(),closing);assert.equal(session.current(closing),true)
  const reply=events.at(-1);assert.equal(reply.phase,'closing');assert.equal(reply.until,closing.until);receiveSessionEvent(peer,reply,null,false);assert.equal(peer.snapshot().phase,'closing')
})
test('a discovery from an old tab cannot reopen a locally anonymous session',()=>{
  const session=createSessionState();session.invalidate(session.ticket());const anonymous=session.snapshot()
  session.receive({id:'old-bootstrap',previous:'old-bootstrap',owner:'owner-a',phase:'active',bootstrap:true})
  assert.equal(session.snapshot(),anonymous)
})
test('an anonymous initial 401 settles locally and cannot create repeated logout broadcasts',()=>{
  let messages=0;const session=createSessionState(()=>messages++)
  assert.equal(session.invalidate(session.ticket()),true)
  assert.equal(session.invalidate(session.ticket()),false)
  assert.equal(messages,0)
})

function browserStorage(){const values=new Map();return{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)}}
function installBrowser(){
  const names=['window','document','localStorage','BroadcastChannel'],saved=names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)])
  globalThis.window=new EventTarget();globalThis.document=new EventTarget();document.visibilityState='visible';globalThis.localStorage=browserStorage();globalThis.BroadcastChannel=undefined
  return()=>{for(const[name,descriptor]of saved){if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name]}}
}
function signIn(){authSession.invalidate(authSession.ticket(),{broadcast:false});return authSession.login('owner-a',authSession.ticket())}
test('storage events still lock another tab when BroadcastChannel is unavailable',()=>{
  const restore=installBrowser();let stop
  try{const current=signIn();stop=startSessionSync();const event={id:'storage-only-logout',previous:current.id,owner:'owner-a',phase:'closing',until:Date.now()+15000};localStorage.setItem('ai-notes:session:v1',JSON.stringify(event));const changed=new Event('storage');Object.assign(changed,{key:'ai-notes:session:v1',newValue:JSON.stringify(event)});window.dispatchEvent(changed);assert.equal(authSession.snapshot().phase,'closing')}finally{stop?.();restore()}
})
test('focus verification remains available when storage and BroadcastChannel are both unavailable',()=>{
  const restore=installBrowser();let stop
  try{signIn();Object.defineProperty(globalThis,'localStorage',{configurable:true,get(){throw new DOMException('blocked','SecurityError')}});let verified=0;stop=startSessionSync(()=>verified++);window.dispatchEvent(new Event('focus'));assert.equal(verified,1)}finally{stop?.();restore()}
})
test('an abandoned closing tab has a bounded barrier and cannot later lock a new login',async()=>{
  const restore=installBrowser();let stop
  try{signIn();stop=startSessionSync();const closing=authSession.beginLogout(authSession.ticket(),Date.now()+20);await new Promise(resolve=>setTimeout(resolve,40));assert.equal(authSession.snapshot().phase,'anonymous');const next=authSession.login('owner-a',authSession.ticket());assert.equal(authSession.invalidate(closing),false);assert.equal(authSession.snapshot(),next)}finally{stop?.();restore()}
})
