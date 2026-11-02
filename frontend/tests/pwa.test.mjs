import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import {boundedPushCleanup,disableOwnedPush,enablePush,canTestPush,sendTestPush,pushTestRequestKey} from '../src/features/pwa/pushCore.ts'

const config={enabled:true,publicVapid:'BA'}
const makeSubscription=()=>({toJSON:()=>({endpoint:'https://fcm.googleapis.com/send/private',keys:{p256dh:'public',auth:'private'}}),unsubscribe:async()=>true})

test('test request key survives retry/remount and changes only with tab session or device association',()=>{
  const values=new Map(),storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)}
  let sequence=0;const create=()=>String(++sequence)
  assert.equal(pushTestRequestKey(storage,'session-a','device-a',create),'1')
  assert.equal(pushTestRequestKey(storage,'session-a','device-a',create),'1')
  assert.equal(pushTestRequestKey(storage,'session-b','device-a',create),'2')
  assert.equal(pushTestRequestKey(storage,'session-a','device-b',create),'3')
  assert.throws(()=>pushTestRequestKey({getItem:()=>null,setItem:()=>{throw Error('storage full')}},'scope','id',create),/storage full/)
})

test('test button eligibility requires configured enabled registration in this owner and tab session',async()=>{
  const binding={id:'target',owner:'alice',scope:'session-a'},rows=[{id:'target',enabled:true}]
  assert.equal(canTestPush(binding,'alice','session-a',config,rows),true)
  for(const [current,owner,scope,cfg,status] of [
    [null,'alice','session-a',config,rows],[binding,'bob','session-a',config,rows],
    [binding,'alice','session-b',config,rows],[binding,'alice','session-a',{enabled:false,publicVapid:null},rows],
    [binding,'alice','session-a',config,[{id:'target',enabled:false}]],[binding,'alice','session-a',config,[]],
    [binding,'alice','session-a',{enabled:true,publicVapid:null},rows],
  ]){
    assert.equal(canTestPush(current,owner,scope,cfg,status),false)
    await assert.rejects(sendTestPush({binding:current,owner,scope,config:cfg,rows:status,requestKey:'same',check:()=>{},api:async()=>{throw Error('must not send')}}),/当前会话/)
  }
})
test('test reminder failures and repeated clicks reuse request key and only send selected device',async()=>{
  const calls=[],receipt={id:'job-1',kind:'notification.test',status:'queued'}
  const options={binding:{id:'target',owner:'alice',scope:'session-a'},owner:'alice',scope:'session-a',config,rows:[{id:'target',enabled:true},{id:'other',enabled:true}],requestKey:'stable-uuid',check:()=>{},api:async(path,method,body)=>{
    calls.push({path,method,body});if(calls.length===1)throw Error('lost response');return receipt
  }}
  await assert.rejects(sendTestPush(options),/lost response/)
  assert.deepEqual(await sendTestPush(options),receipt)
  assert.deepEqual(await sendTestPush(options),receipt)
  assert.equal(calls.length,3)
  for(const call of calls)assert.deepEqual(call,{path:'/push/test',method:'POST',body:{request_key:'stable-uuid',subscription_id:'target'}})
})
test('test reminder rejects a session transition before request and ignores its delayed response',async()=>{
  let current=true,sent=0
  const options={binding:{id:'target',owner:'alice',scope:'scope'},owner:'alice',scope:'scope',config,rows:[{id:'target',enabled:true}],requestKey:'stable',check:()=>{if(!current)throw Error('session changed')},api:async()=>{sent++;current=false;return {id:'job'}}}
  await assert.rejects(sendTestPush(options),/session changed/)
  await assert.rejects(sendTestPush(options),/session changed/)
  assert.equal(sent,1)
})

test('settings renders an explicitly queued test action and disables it for unusable or pending bindings',async()=>{
  const source=readFileSync(new URL('../src/features/pwa/PwaSettings.tsx',import.meta.url),'utf8')
  const {outputText}=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}})
  function render({cfg=config,binding={id:'target',owner:'alice',scope:'session-a'},rows=[{id:'target',enabled:true}],scope='session-a',pending=false,error=null}={}){
    const exports={},messages=[],sent=[];let mutationIndex=0
    const element=(type,props)=>({type,props})
    const dependencies={
      'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
      react:{useEffect:()=>{},useState:initial=>[initial,value=>messages.push(value)]},
      '@tanstack/react-query':{
        useQuery:({queryKey})=>({data:queryKey[0]==='push-config'?cfg:rows,refetch:()=>{}}),
        useMutation:options=>{const index=mutationIndex++;return {isPending:index===0&&pending,error:index===0?error:null,mutate:async()=>{const receipt=await options.mutationFn();options.onSuccess?.(receipt)}}},
      },
      '../../api':{api:async()=>{}},'../../session.ts':{readSession:()=>({scope})},
      './push':{readBinding:()=>binding,pushSupported:()=>true,sendBrowserPushTest:async(owner,configuration)=>{sent.push({owner,configuration});return {id:'job',status:'queued'}}},
      './register':{pwaSnapshot:()=>({}),subscribePwa:()=>()=>{}},'./pushCore.ts':{canTestPush},
    }
    vm.runInNewContext(outputText,{exports,require:name=>{if(!(name in dependencies))throw Error('unknown import '+name);return dependencies[name]}})
    const tree=exports.default({owner:'alice'}),nodes=[]
    const visit=node=>{if(!node||typeof node!=='object')return;if(Array.isArray(node)){node.forEach(visit);return}nodes.push(node);visit(node.props?.children)}
    visit(tree)
    return {button:nodes.find(node=>node.type==='button'&&node.props.children==='发送测试提醒'),alerts:nodes.filter(node=>node.props?.role==='alert'),sent,messages}
  }
  const active=render();assert.equal(active.button.props.disabled,false)
  await active.button.props.onClick()
  assert.equal(active.sent.length,1)
  assert.match(active.messages[0],/已排队.*不保证设备送达/)
  for(const options of [{cfg:{enabled:false,publicVapid:null}},{binding:null},{scope:'session-b'},{rows:[{id:'target',enabled:false}]},{pending:true}])assert.equal(render(options).button.props.disabled,true)
  assert.equal(render({error:Error('重试仍使用原请求标识')}).alerts[0].props.children,'重试仍使用原请求标识')
})

test('permission is requested before any network await; denied leaves in-app notifications and subscription intact',async()=>{
  const calls=[]
  await assert.rejects(enablePush({config,permission:async()=>{calls.push('permission');return 'denied'},api:async()=>{calls.push('network')},check:()=>{},registration:{pushManager:{getSubscription:async()=>calls.push('subscription')}}}),/权限/)
  assert.deepEqual(calls,['permission'])
})
test('disabled config requests no permission or subscription and leaves in-app notifications usable',async()=>{
  let requested=false
  await assert.rejects(enablePush({config:{enabled:false,publicVapid:null},permission:async()=>{requested=true;return 'granted'},check:()=>{},api:async()=>{throw Error('unexpected')},registration:{}}),/尚未配置/)
  assert.equal(requested,false)
})
test('existing shared subscription survives B tab server conflict without unsubscribe',async()=>{
  let unsubscribed=0,created=0
  const subscription={...makeSubscription(),options:{applicationServerKey:new Uint8Array([9]).buffer},unsubscribe:async()=>++unsubscribed}
  await assert.rejects(enablePush({config,permission:async()=>'granted',check:()=>{},registration:{pushManager:{getSubscription:async()=>subscription,subscribe:async()=>{created++;return subscription}}},api:async(path,method,data)=>{assert.equal(method,'PUT');assert.equal(data.endpoint,subscription.toJSON().endpoint);throw Error('409 other session')}}),/409/)
  assert.equal(unsubscribed,0);assert.equal(created,0)
})
test('explicit same-owner rebind safely rotates old VAPID key only AFTER server proves ownership',async()=>{
  const order=[]
  const old={...makeSubscription(),options:{applicationServerKey:new Uint8Array([9]).buffer},unsubscribe:async()=>{order.push('unsubscribe-own-old-key');return true}}
  const registration={pushManager:{getSubscription:async()=>old,subscribe:async options=>{assert.deepEqual([...options.applicationServerKey],[4]);order.push('create-new-key');return makeSubscription()}}}
  let puts=0
  const result=await enablePush({config,permission:async()=>'granted',check:()=>{},registration,api:async(path,method)=>{
    order.push(method)
    return method==='PUT'?{id:++puts===1?'owned-old':'owned-new',enabled:true}:undefined
  }})
  assert.equal(result.id,'owned-new')
  assert.deepEqual(order,['PUT','unsubscribe-own-old-key','create-new-key','PUT','DELETE'])
})
test('new subscription registers once; delayed account switch never mutates another session',async()=>{
  let calls=0,switched=false
  const subscription=makeSubscription()
  const registration={pushManager:{getSubscription:async()=>null,subscribe:async(options)=>{assert.equal(options.userVisibleOnly,true);return subscription}}}
  const result=await enablePush({config,permission:async()=>'granted',check:()=>{},registration,api:async()=>{calls++;return {id:'sub',enabled:true}}})
  assert.equal(result.id,'sub');assert.equal(calls,1)
  await assert.rejects(enablePush({config,permission:async()=>{switched=true;return 'granted'},check:()=>{if(switched)throw Error('session changed')},registration,api:async()=>{calls++;}}),/session changed/)
  assert.equal(calls,1)
})
test('B tab logout never touches A browser subscription, even with copied local metadata',async()=>{
  let calls=0,unsubscribed=0
  const registration={pushManager:{getSubscription:async()=>({unsubscribe:async()=>unsubscribed++})}}
  const binding={id:'A-device',owner:'alice',scope:'A-session'}
  assert.equal(await disableOwnedPush({binding,owner:'bob',scope:'B-session',api:async()=>calls++,registration,check:()=>{}}),false)
  assert.equal(await disableOwnedPush({binding,owner:'alice',scope:'B-session',api:async()=>calls++,registration,check:()=>{}}),false)
  assert.equal(await disableOwnedPush({binding,owner:'alice',scope:'A-session',api:async()=>{calls++;return [{id:'B-device',enabled:true}]},registration,check:()=>{}}),false)
  assert.equal(calls,1);assert.equal(unsubscribed,0)
})
test('server ownership failure never unsubscribes; confirmed owner cancels browser before releasing binding',async()=>{
  const order=[],binding={id:'own',owner:'alice',scope:'session'}
  const registration={pushManager:{getSubscription:async()=>({unsubscribe:async()=>{order.push('browser-unsubscribe');return true}})}}
  const api=async(path,method)=>{order.push(method||'GET');return method?undefined:[{id:'own',enabled:true}]}
  assert.equal(await disableOwnedPush({binding,owner:'alice',scope:'session',registration,api,check:()=>{}}),true)
  assert.deepEqual(order,['GET','browser-unsubscribe','DELETE'])
  order.length=0
  await assert.rejects(disableOwnedPush({binding,owner:'alice',scope:'session',registration,api:async()=>{throw Error('network')},check:()=>{}}),/network/)
  assert.deepEqual(order,[])
})
test('stalled or failed browser cleanup never blocks server logout and aborts delayed network work',async()=>{
  let signal
  await boundedPushCleanup(async value=>{signal=value;await new Promise(()=>{})},5)
  assert.equal(signal.aborted,true)
  await boundedPushCleanup(async()=>{throw Error('offline')},5)
})

function workerHarness(){
  const listeners={},stores=new Map(),deleted=[],network=[],notices=[],opened=[]
  const storage={open:async name=>{
    if(!stores.has(name))stores.set(name,new Map())
    const map=stores.get(name)
    return {addAll:async paths=>paths.forEach(path=>map.set(path,new Response(path))),match:async path=>map.get(path)?.clone()}
  },keys:async()=>[...stores.keys()],delete:async name=>{deleted.push(name);return stores.delete(name)},match:async path=>{
    for(const map of stores.values())if(map.has(path))return map.get(path).clone()
  }}
  const self={location:{origin:'https://notes.example'},addEventListener:(name,fn)=>listeners[name]=fn,
    skipWaiting:()=>{self.skipped=true},registration:{showNotification:async(title,options)=>notices.push({title,...options})},clients:{claim:async()=>{},openWindow:async url=>opened.push(url)}}
  const source=readFileSync(new URL('../src/features/pwa/sw-template.js',import.meta.url),'utf8').replace('__SHELL_VERSION__','"new"').replace('__SHELL_ASSETS__',JSON.stringify(['/index.html','/assets/main-HASH.js','/assets/style-HASH.css','/assets/font-HASH.woff2']))
  let online=true
  vm.runInNewContext(source,{self,caches:storage,URL,fetch:async request=>{network.push(request.url);if(!online)throw Error('offline');return new Response('network')}})
  async function dispatch(name,data={}){let wait;listeners[name]({...data,waitUntil:value=>wait=value});await wait}
  function fetchEvent(url,options={}){let response;listeners.fetch({request:{url,method:'GET',mode:'cors',...options},respondWith:value=>response=value});return response}
  return {self,storage,stores,deleted,network,notices,opened,dispatch,fetchEvent,offline:()=>online=false}
}
test('offline shell includes JS CSS fonts; APIs credentials external requests and private routes are never cached',async()=>{
  const w=workerHarness();await w.dispatch('install');w.offline()
  assert.equal(await (await w.fetchEvent('https://notes.example/',{mode:'navigate'})).text(),'/index.html')
  for(const path of ['/assets/main-HASH.js','/assets/style-HASH.css','/assets/font-HASH.woff2'])assert.equal(await (await w.fetchEvent('https://notes.example'+path)).text(),path)
  for(const path of ['/api/v1/notes','/api/v1/auth/me','/private/file','/?token=secret','/assets/main-HASH.js?token=secret'])assert.equal(w.fetchEvent('https://notes.example'+path),undefined)
  assert.equal(w.fetchEvent('https://evil.example/assets/main-HASH.js'),undefined)
  assert.equal(w.fetchEvent('https://notes.example/assets/main-HASH.js',{method:'POST'}),undefined)
  assert.equal([...w.stores.values()][0].has('/api/v1/notes'),false)
})
test('update waits for user request, retains prior bundle and never forcibly reloads open editors',async()=>{
  const w=workerHarness()
  await w.storage.open('ai-notes-shell-oldest');await w.storage.open('ai-notes-shell-prior')
  await w.dispatch('install');assert.equal(w.self.skipped,undefined)
  await w.dispatch('activate');assert.deepEqual(w.deleted,['ai-notes-shell-oldest'])
  assert.equal(w.self.skipped,undefined)
  await w.dispatch('message',{data:{type:'ACTIVATE_UPDATE'}});assert.equal(w.self.skipped,true)
  assert.deepEqual(w.opened,[])
})
test('push payload cannot expose private text or deep links and click only opens same-origin root',async()=>{
  const w=workerHarness()
  await w.dispatch('push',{data:{json:()=>({body:'private text',url:'https://evil.example/?token=secret'})}})
  assert.equal(w.notices[0].body,'知页有新的站内提醒');assert.equal(w.notices[0].tag,'notes-notification')
  assert.deepEqual(Object.keys(w.notices[0].data),[])
  await w.dispatch('notificationclick',{notification:{data:{url:'https://evil.example'},close:()=>{}}})
  assert.deepEqual(w.opened,['https://notes.example/'])
})
test('manifest has install metadata and production plugin inventories all bundle assets',()=>{
  const manifest=JSON.parse(readFileSync(new URL('../public/manifest.webmanifest',import.meta.url),'utf8'))
  assert.equal(manifest.display,'standalone');assert.equal(manifest.start_url,'/')
  assert.ok(manifest.icons.some(icon=>icon.sizes==='192x192'))
  assert.ok(manifest.icons.some(icon=>icon.sizes==='512x512'))
})

test('install prompt is held until explicit click; production registers with updateViaCache none and activation is explicit',async()=>{
  const events={},swEvents={},registrationEvents={},sent=[],registered=[]
  const registration={waiting:null,addEventListener:(name,fn)=>registrationEvents[name]=fn,update:async()=>{}}
  const originalWindow=globalThis.window,originalNavigator=Object.getOwnPropertyDescriptor(globalThis,'navigator')
  globalThis.window={isSecureContext:true,matchMedia:()=>({matches:false}),addEventListener:(name,fn)=>events[name]=fn}
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{serviceWorker:{controller:{},addEventListener:(name,fn)=>swEvents[name]=fn,register:async(...args)=>{registered.push(args);return registration}}}})
  try{
    const source=readFileSync(new URL('../src/features/pwa/register.ts',import.meta.url),'utf8').replace('import.meta.env.PROD','true')
    const {outputText}=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}})
    const pwa=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'))
    pwa.registerPwa();await Promise.resolve();await Promise.resolve()
    assert.deepEqual(registered,[['/sw.js',{scope:'/',updateViaCache:'none'}]])
    let prompted=0,prevented=0
    events.beforeinstallprompt({preventDefault:()=>prevented++,prompt:async()=>prompted++,userChoice:Promise.resolve({outcome:'accepted'})})
    assert.equal(prompted,0);assert.equal(prevented,1);assert.equal(pwa.pwaSnapshot().installAvailable,true)
    await pwa.installApp();assert.equal(prompted,1);assert.equal(pwa.pwaSnapshot().installAvailable,false)
    events.appinstalled();assert.equal(pwa.pwaSnapshot().installed,true)
    registration.waiting={postMessage:value=>sent.push(value)}
    registration.installing={addEventListener:(_,fn)=>fn()};registrationEvents.updatefound()
    assert.equal(pwa.pwaSnapshot().waiting,true);assert.deepEqual(sent,[])
    pwa.activateUpdate();assert.deepEqual(sent,[{type:'ACTIVATE_UPDATE'}])
    swEvents.controllerchange();assert.equal(pwa.pwaSnapshot().activated,true)
  }finally{
    globalThis.window=originalWindow
    if(originalNavigator)Object.defineProperty(globalThis,'navigator',originalNavigator);else delete globalThis.navigator
  }
})
