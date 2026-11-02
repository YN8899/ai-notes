import test from 'node:test'
import assert from 'node:assert/strict'
import {createHeader,unlockHeader,seal,open,encode,decode,ITERATIONS,sha256} from '../src/features/offline/crypto.ts'
import {equal,threeWay,applyMappings,mapPayload} from '../src/features/offline/merge.ts'
import {emptyWorkspace,kinds} from '../src/features/offline/types.ts'
import {queueOperation,edit,addImage,addPdf,copyNote} from '../src/features/offline/queue.ts'
import {OfflineRepository} from '../src/features/offline/repository.ts'
const owner='00000000-0000-4000-8000-000000000001',other='00000000-0000-4000-8000-000000000002',epoch='00000000-0000-4000-8000-000000000003'
let shared
test.before(async()=>{shared=await createHeader(owner,'local-only-passphrase-123')})
test('wrapped random AES256 key unlocks with independent local passphrase and rejects altered PBKDF2 bounds',async()=>{
  assert.equal(shared.header.iterations,600000);assert.equal(ITERATIONS,600000)
  const key=await unlockHeader(shared.header,'local-only-passphrase-123')
  assert.equal(key.extractable,false)
  const envelope=await seal(key,owner,'records','note-1',encode({title:'私密标题',body:'完整正文'}))
  assert.deepEqual(decode(await open(shared.key,owner,'records','note-1',envelope)),{title:'私密标题',body:'完整正文'})
  assert.equal(Buffer.from(envelope.ciphertext).includes(Buffer.from('完整正文')),false)
  await assert.rejects(unlockHeader(shared.header,'wrong-passphrase-123'),/口令不正确/)
  for(const iterations of [1,599999,600001,Infinity,1e12])await assert.rejects(unlockHeader({...shared.header,iterations},'local-only-passphrase-123'),/参数/)
})
test('every encryption has a fresh IV and AAD binds owner, store, key and schema',async()=>{
  const a=await seal(shared.key,owner,'assets','blob-1',encode({filename:'private.png'})),b=await seal(shared.key,owner,'assets','blob-1',encode({filename:'private.png'}))
  assert.notDeepEqual(a.iv,b.iv);assert.notDeepEqual(a.ciphertext,b.ciphertext)
  for(const args of [[other,'assets','blob-1'],[owner,'records','blob-1'],[owner,'assets','blob-2']])await assert.rejects(open(shared.key,...args,a))
  const corrupt={...a,ciphertext:a.ciphertext.slice(0)};new Uint8Array(corrupt.ciphertext)[0]^=1
  await assert.rejects(open(shared.key,owner,'assets','blob-1',corrupt))
})
test('asset SHA256 uses actual bytes',async()=>{assert.equal(await sha256(new TextEncoder().encode('abc').buffer),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')})
test('three-way merge combines non-overlapping fields and identical concurrent values',()=>{
  const base={title:'旧',notebook_id:'A',tag_ids:['x'],body_json:{type:'doc',content:[]},plain_text:''},local={...base,title:'本机'},server={...base,tag_ids:['y']}
  assert.deepEqual(threeWay(base,local,server).conflicts,[])
  assert.deepEqual(threeWay(base,local,server).merged,{...server,title:'本机'})
  assert.deepEqual(threeWay(base,local,{...server,title:'本机'}).conflicts,[])
  assert.deepEqual(threeWay(base,local,{...server,title:'服务端'}).conflicts,['title'])
})
test('body_json and plain_text conflict as one atom; page order is atomic',()=>{
  const base={body_json:{text:'old'},plain_text:'old'},local={body_json:{text:'new'},plain_text:'old'},server={body_json:{text:'old'},plain_text:'server'}
  assert.deepEqual(threeWay(base,local,server).conflicts,['body'])
  assert.deepEqual(threeWay({page_ids:['a','b']},{page_ids:['b','a']},{page_ids:['a','c','b']}).conflicts,['page_ids'])
})
test('ID mappings change only controlled IDs and never UUID-looking prose or a sent payload',()=>{
  const w=emptyWorkspace();w.syncEpoch=epoch
  w.notes.push({id:owner,revision:1,title:owner,body_json:{type:'doc',content:[{text:owner}]},plain_text:owner,kind:'text',notebook_id:owner,tag_ids:[owner],deleted_at:null})
  const sent=queueOperation(w,'note.update',owner,{title:owner,plain_text:owner,body_json:{type:'doc',content:[{text:owner}]},notebook_id:owner,tag_ids:[owner]},{revision:1})
  sent.sent=true;sent.state='sending';const original=structuredClone(sent.operation)
  const waiting=queueOperation(w,'note.update',owner,{title:owner,notebook_id:owner},{revision:1})
  applyMappings(w,{[owner]:other})
  assert.deepEqual(sent.operation,original)
  assert.equal(waiting.operation.resource_id,other);assert.equal(waiting.operation.payload.notebook_id,other);assert.equal(waiting.operation.payload.title,owner)
  assert.equal(w.notes[0].id,other);assert.equal(w.notes[0].title,owner);assert.equal(w.notes[0].plain_text,owner);assert.equal(w.notes[0].body_json.content[0].text,owner)
  assert.deepEqual(mapPayload({page_ids:[owner],note_id:owner,url:owner,filename:owner},{[owner]:other}),{page_ids:[other],note_id:other,url:owner,filename:owner})
})
test('queue requires a captured epoch and freezes copied payload independently of editor references',()=>{
  const w=emptyWorkspace();assert.throws(()=>queueOperation(w,'note.create',owner,{title:'a'}),/同步代次/)
  w.syncEpoch=epoch;const payload={title:'a',body_json:{type:'doc',content:[{text:'before'}]}},q=queueOperation(w,'note.create',owner,payload)
  payload.body_json.content[0].text='later';payload.title='later'
  assert.equal(q.operation.sync_epoch,epoch);assert.equal(q.operation.payload.title,'a');assert.equal(q.operation.payload.body_json.content[0].text,'before')
  assert.equal(q.operation.base_revision,null)
  const next=queueOperation(w,'note.update',owner,{title:'b'},{revision:7})
  assert.equal(next.operation.base_revision,7);assert.deepEqual(next.depends,[q.operation.operation_id])
  assert.equal(new Set([q.operation.operation_id,next.operation.operation_id]).size,2)
  assert.equal(kinds.length,19)
})
// This adapter tests repository encryption/transaction contract only. It is not an IndexedDB implementation.
function contractAdapter(initial){
  let row=initial,fail=false,commits=0
  return {get row(){return row},get commits(){return commits},set fail(value){fail=value},db:{read:async(_cap,key)=>key==='workspace'?row:undefined,put:async(cap,version,rows)=>{cap.check();if(fail)throw new DOMException('quota','QuotaExceededError');assert.equal(row.version,version);row=structuredClone(rows.find(r=>r.id.endsWith(':workspace')));commits++}}}
}
async function repository(){
  let live=true;const cap={owner,generation:1,key:shared.key,signal:new AbortController().signal,check:()=>{if(!live)throw new DOMException('locked','AbortError')}}
  const initial=emptyWorkspace();initial.syncEpoch=epoch
  const sealed=await seal(shared.key,owner,'records','workspace',encode(initial)),adapter=contractAdapter({...sealed,id:`${owner}:workspace`,owner,version:1})
  return {repo:new OfflineRepository(cap,adapter.db),adapter,lock:()=>{live=false}}
}
test('local projection and queue reach one encrypted commit; quota failure does not publish either',async()=>{
  const {repo,adapter}=await repository()
  await edit(repo,'note.create',other,{title:'完整本机笔记',kind:'text',body_json:{},plain_text:'',notebook_id:null,tag_ids:[]})
  assert.equal(adapter.commits,1);let value=await repo.value();assert.equal(value.notes.length,1);assert.equal(value.queue.length,1)
  const before=structuredClone(adapter.row);adapter.fail=true
  await assert.rejects(edit(repo,'note.update',other,{title:'尚未保存'}),{name:'QuotaExceededError'})
  assert.deepEqual(adapter.row,before);adapter.fail=false;value=await repo.value();assert.equal(value.notes[0].title,'完整本机笔记');assert.equal(value.queue.length,1)
  assert.equal(Buffer.from(adapter.row.ciphertext).includes(Buffer.from('完整本机笔记')),false)
})
test('capability invalidation after async read forbids decryption result publication or commits',async()=>{
  const {repo,adapter,lock}=await repository();const pending=repo.value();lock();await assert.rejects(pending,{name:'AbortError'});await assert.rejects(repo.mutate(()=>{}),{name:'AbortError'});assert.equal(adapter.commits,0)
})
test('image upload phases and later page edits depend on durable completion; reload retains operation IDs and Blob reference',async()=>{
  const {repo}=await repository();await edit(repo,'note.create',other,{title:'images',kind:'image'})
  await addImage(repo,other,realPng(),'私人文件.png')
  const value=await repo.value(),create=value.queue.find(q=>q.operation.kind==='upload.create'),complete=value.queue.find(q=>q.operation.kind==='upload.complete')
  assert.ok(create&&complete);assert.equal(create.continuation,complete.operation.operation_id);assert.ok(complete.depends.includes(create.operation.operation_id));assert.equal(complete.assetKey,create.assetKey)
  assert.equal(create.operation.payload.filename,'私人文件.png');assert.equal('upload_url'in create.operation.payload,false)
  await edit(repo,'page.remove',value.pages[0].id,{note_id:other})
  const reloaded=await repo.value(),remove=reloaded.queue.at(-1);assert.ok(remove.depends.includes(complete.operation.operation_id));assert.equal(reloaded.queue.find(q=>q.operation.kind==='upload.create').operation.operation_id,create.operation.operation_id)
})
test('offline PDF keeps original encrypted asset reference and explicitly has no fabricated pages',async()=>{
  const {repo}=await repository();const id=await addPdf(repo,realPdf(),'学习原件.pdf')
  const value=await repo.value();assert.equal(value.pages.length,0);assert.equal(value.notes[0].id,id);assert.equal(value.pdf_imports[0].status,'local_pending');assert.equal(value.queue[0].operation.kind,'pdf.create');assert.equal(value.queue[1].operation.kind,'pdf.start');assert.ok(value.queue[1].depends.includes(value.queue[0].operation.operation_id))
})
test('image copy refuses empty or missing originals instead of reporting a fake successful copy',async()=>{
  const {repo}=await repository();await edit(repo,'note.create',other,{title:'missing',kind:'image'})
  await assert.rejects(copyNote(repo,other),/真实页面/)
  const value=await repo.value();assert.equal(value.notes.length,1)
})


test('rejects disguised image/PDF before any encrypted projection or queue commit',async()=>{
  const {repo,adapter}=await repository();await edit(repo,'note.create',other,{title:'images',kind:'image'})
  const before=adapter.commits
  await assert.rejects(addImage(repo,other,new Blob(['not an image'],{type:'image/png'}),'fake.png'))
  await assert.rejects(addPdf(repo,new Blob(['%PDF-not a PDF'],{type:'application/pdf'}),'fake.pdf'))
  assert.equal(adapter.commits,before)
  const value=await repo.value();assert.equal(value.pages.length,0);assert.equal(value.files.length,0);assert.equal(value.queue.length,1)
})

// Real PNG decoding in Node uses PDF.js's installed optional native canvas adapter.
// Browser validation still uses the platform createImageBitmap, independently checked in browser QA.
const {loadImage,createCanvas}=await import('@napi-rs/canvas')
globalThis.createImageBitmap=async blob=>{const image=await loadImage(Buffer.from(await blob.arrayBuffer()));return {width:image.width,height:image.height,close(){}}}
const realPng=()=>new Blob([createCanvas(1,1).toBuffer('image/png')],{type:'image/png'})
function realPdf(pages=1,width=612,height=792){
  const objects=[`<< /Type /Catalog /Pages 2 0 R >>`,`<< /Type /Pages /Kids [${Array.from({length:pages},(_,i)=>`${3+i} 0 R`).join(' ')}] /Count ${pages} >>`,...Array.from({length:pages},()=>`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${width} ${height}] /Resources << >> >>`)]
  let data='%PDF-1.7\n',offsets=[0]
  for(let i=0;i<objects.length;i++){offsets.push(Buffer.byteLength(data));data+=`${i+1} 0 obj\n${objects[i]}\nendobj\n`}
  const xref=Buffer.byteLength(data);data+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`+offsets.slice(1).map(n=>String(n).padStart(10,'0')+' 00000 n \n').join('')+`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return new Blob([data],{type:'application/pdf'})
}
test('real decoder rejects corrupt pixels and oversized image header before projection',async()=>{
  const {repo,adapter}=await repository();await edit(repo,'note.create',other,{title:'images',kind:'image'})
  const before=adapter.commits,bytes=new Uint8Array(await realPng().arrayBuffer())
  await assert.rejects(addImage(repo,other,new Blob([bytes.subarray(0,33)],{type:'image/png'}),'corrupt.png'))
  const huge=new Uint8Array(await realPng().arrayBuffer());new DataView(huge.buffer).setUint32(16,400000)
  await assert.rejects(addImage(repo,other,new Blob([huge],{type:'image/png'}),'huge.png'),/像素/)
  assert.equal(adapter.commits,before)
})
test('PDF parser enforces page count and page pixel boundaries without committing',async()=>{
  const {repo,adapter}=await repository()
  await assert.rejects(addPdf(repo,realPdf(201),'too-many.pdf'),/1–200/)
  await assert.rejects(addPdf(repo,realPdf(1,100000,100000),'too-large.pdf'),/像素/)
  assert.equal(adapter.commits,0)
})
const {affectedByDeletion,settleDeletion}=await import('../src/features/offline/deletion.ts')
function deletedOrganization(kind='notebook'){
  const w=emptyWorkspace();w.syncEpoch=epoch
  w[kind==='folder'?'folders':kind==='notebook'?'notebooks':'tags'].push({id:owner,revision:1,name:'local organization'})
  const q=queueOperation(w,`${kind}.update`,owner,{name:'local organization'},{id:owner,revision:1,name:'old'})
  q.state='conflict';q.conflict={base:q.base,local:{...q.base,...q.operation.payload},server:{resource_kind:kind,exists:false},fields:['resource'],message:'deleted'}
  const payload={title:'pending preserved',kind:'text',notebook_id:kind==='notebook'?owner:null,tag_ids:kind==='tag'?[owner]:[],plain_text:'complete body',body_json:{}}
  w.notes.push({id:other,revision:1,deleted_at:null,...payload})
  const dependent=queueOperation(w,'note.create',other,payload);dependent.depends.push(q.operation.operation_id)
  const unrelated=queueOperation(w,'note.create',epoch,{title:'unrelated',kind:'text'})
  return {w,q,dependent,unrelated}
}
test('explicit adopt organization deletion preserves dependent note work, unrelated queue and history',()=>{
  for(const kind of ['folder','notebook','tag']){
    const {w,q,dependent,unrelated}=deletedOrganization(kind),before=structuredClone(unrelated)
    assert.equal(affectedByDeletion(w,q).length,2);settleDeletion(w,q,'server')
    assert.equal(q.state,'done');assert.equal(dependent.state,'pending');assert.equal(w.notes[0].plain_text,'complete body');assert.equal(w.notes[0].notebook_id,null);assert.deepEqual(w.notes[0].tag_ids,[])
    assert.equal(dependent.operation.payload.notebook_id,null);assert.deepEqual(dependent.operation.payload.tag_ids,[]);assert.deepEqual(unrelated,before)
    assert.ok(w.history.some(h=>h.value.reason==='server_deleted_accepted'))
    assert.ok(w.queue.filter(q=>q.state!=='done').every(q=>q.depends.every(id=>w.queue.some(parent=>parent.operation.operation_id===id))))
  }
})
test('explicit deleted organization copy uses new identity and remaps pending dependent reference',()=>{
  const {w,q,dependent,unrelated}=deletedOrganization(),before=structuredClone(unrelated),newId=settleDeletion(w,q,'copy')
  assert.notEqual(newId,owner);assert.equal(w.notebooks[0].id,newId);assert.equal(w.notes[0].notebook_id,newId);assert.equal(dependent.operation.payload.notebook_id,newId)
  const created=w.queue.find(q=>q.operation.kind==='notebook.create');assert.ok(created);assert.ok(dependent.depends.includes(created.operation.operation_id));assert.equal(q.state,'done');assert.deepEqual(unrelated,before)
})
test('unknown server state and sent dependent cannot be silently resurrected',()=>{
  const a=deletedOrganization();a.q.conflict.server=null;assert.throws(()=>settleDeletion(a.w,a.q,'server'),/明确/)
  const b=deletedOrganization();b.dependent.sent=true;assert.throws(()=>settleDeletion(b.w,b.q,'copy'),/已经发送/)
})
globalThis.BroadcastChannel=undefined
const {fresh}=await import('../src/features/offline/sync.ts')
const {OfflineHttpError}=await import('../src/features/offline/network.ts')
const {logoutWarning}=await import('../src/features/offline/logout-guard.ts')
test('organization GET 404 is a verified exists:false current state, not a null dead end',async()=>{
  const {w,q}=deletedOrganization()
  assert.deepEqual(await fresh({json:async()=>{throw new OfflineHttpError(404,'not_found','absent')}},q,w),{resource_kind:'notebook',exists:false})
  await assert.rejects(fresh({json:async()=>{throw new OfflineHttpError(500,'failed','failed')}},q,w),/failed/)
})
test('logout warning explicitly states whole-vault deletion and exact or unknown unsynced risk',()=>{
  assert.match(logoutWarning(3),/3 项未同步/);assert.match(logoutWarning(0),/0 项未同步/);assert.match(logoutWarning(null),/数量未知/)
  for(const count of [0,3,null]){assert.match(logoutWarning(count),/整个加密离线资料库/);assert.match(logoutWarning(count),/取消/)}
})


const {readFile}=await import('node:fs/promises')
test('actual password-protected PDF is rejected before projection',async()=>{
  const {repo,adapter}=await repository(),bytes=await readFile(new URL('../../backend/tests/fixtures/password-required.pdf',import.meta.url))
  await assert.rejects(addPdf(repo,new Blob([bytes],{type:'application/pdf'}),'encrypted.pdf'),/密码/)
  assert.equal(adapter.commits,0)
})


test('stale note expected baseline refuses overlapping local body instead of rebasing stale payload',async()=>{
  const {repo,adapter}=await repository();await edit(repo,'note.create',other,{title:'old title',kind:'text',body_json:{},plain_text:'old body'})
  const baseline=structuredClone((await repo.value()).notes[0])
  await edit(repo,'note.update',other,{body_json:{type:'doc',content:[]},plain_text:'A body'},baseline)
  const commits=adapter.commits
  await assert.rejects(edit(repo,'note.update',other,{body_json:{},plain_text:'B body'},baseline),/本机.*冲突/)
  assert.equal(adapter.commits,commits);assert.equal((await repo.value()).notes[0].plain_text,'A body')
})
const {saveNoteDraft}=await import('../src/features/offline/queue.ts')
const {noteDraft,changedNoteFields}=await import('../src/features/offline/draft.ts')
test('two encrypted note views save non-overlapping title/body without reverting the new body',async()=>{
  const {repo}=await repository();await edit(repo,'note.create',other,{title:'old',kind:'text',plain_text:'old body',body_json:{}})
  const baseline=structuredClone((await repo.value()).notes[0]),a=noteDraft(baseline),b=noteDraft(baseline)
  a.body_json={type:'doc',content:[]};a.plain_text='A saved body';await saveNoteDraft(repo,other,baseline,a)
  b.title='B title';await saveNoteDraft(repo,other,baseline,b)
  const w=await repo.value();assert.equal(w.notes[0].plain_text,'A saved body');assert.equal(w.notes[0].title,'B title');assert.deepEqual(w.queue.at(-1).operation.payload,{title:'B title'})
  assert.deepEqual(changedNoteFields(noteDraft(baseline),a),{body_json:a.body_json,plain_text:a.plain_text})
})
test('CAS retry reruns expected-baseline conflict check and cannot overwrite another local commit',async()=>{
  const {repo,adapter}=await repository();await edit(repo,'note.create',other,{title:'old',kind:'text',plain_text:'old body',body_json:{}})
  const baseline=structuredClone((await repo.value()).notes[0]),realPut=adapter.db.put
  const {ConcurrentWriteError}=await import('../src/features/offline/database.ts')
  let racing=true
  adapter.db.put=async(cap,version,rows)=>{
    if(racing){racing=false;await edit(repo,'note.update',other,{plain_text:'A wins race',body_json:{type:'doc',content:[]}},baseline);throw new ConcurrentWriteError()}
    return realPut(cap,version,rows)
  }
  await assert.rejects(edit(repo,'note.update',other,{plain_text:'B must not overwrite',body_json:{}},baseline),/本机编辑冲突/)
  assert.equal((await repo.value()).notes[0].plain_text,'A wins race');assert.equal((await repo.value()).queue.length,2)
})

test('only revision_conflict can automatically merge; business 409 pauses with the original UUID',async()=>{
  const {signIn}=await import('./tab-environment.mjs'),{syncQueue}=await import('../src/features/offline/sync.ts')
  const savedFetch=globalThis.fetch,savedNavigator=Object.getOwnPropertyDescriptor(globalThis,'navigator')
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{locks:{request:async(_name,_options,fn)=>fn({})}}})
  try{
    for(const code of ['folder_cycle','tag_name_taken','revision_conflict']){
      signIn(owner,'s')
      const kind=code==='tag_name_taken'?'tag':'folder',row={id:other,revision:1,name:'base',parent_id:null},state=emptyWorkspace();state.syncEpoch=epoch;state[kind==='folder'?'folders':'tags'].push(structuredClone(row))
      const original=queueOperation(state,`${kind}.update`,other,{name:'local'},{...row}),requests=[]
      let current=structuredClone(row),value=state
      const fakeRepo={cap:{owner,signal:new AbortController().signal,check(){}},value:async()=>structuredClone(value),mutate:async change=>{const copy=structuredClone(value),result=change(copy);value=copy;return result}}
      globalThis.fetch=async(path,init)=>{
        const reply=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'content-type':'application/json'}})
        if(path==='/api/v1/auth/me')return reply({id:owner})
        if(path===`/api/v1/${kind==='folder'?'folders':'tags'}/${other}`)return reply(current)
        if(path==='/api/v1/offline/sync'){
          const op=JSON.parse(init.body);requests.push(op);if(requests.length>3)throw new Error('unexpected unbounded replacement')
          if(code==='revision_conflict'&&requests.length===2){current={...current,...op.payload,revision:3};return reply({operation_id:op.operation_id,kind:op.kind,resource_id:other,resource_mapping:{},applied_revision:3,transport:{}})}
          if(code==='revision_conflict')current={...current,revision:2,parent_id:epoch}
          return reply({code,message:code,current:{resource_kind:kind,exists:true,[kind]:current}},409)
        }
        throw new Error('unexpected path '+path)
      }
      await syncQueue(fakeRepo,()=>{})
      if(code==='revision_conflict'){assert.equal(requests.length,2);assert.equal(value.queue.filter(q=>q.state==='done').length,2);assert.notEqual(requests[0].operation_id,requests[1].operation_id)}
      else {assert.equal(requests.length,1);assert.equal(value.queue.length,1);assert.equal(value.queue[0].state,'conflict');assert.equal(value.queue[0].operation.operation_id,original.operation.operation_id)}
    }
  }finally{globalThis.fetch=savedFetch;if(savedNavigator)Object.defineProperty(globalThis,'navigator',savedNavigator);else delete globalThis.navigator}
})


test('JSONB object key order is irrelevant recursively, while arrays and primitive values remain strict',()=>{
  const a={type:'doc',content:[{type:'paragraph',attrs:{align:null,level:1},content:[{type:'text',text:'same',marks:[{type:'bold',attrs:{active:true}}]}]}]}
  const b={content:[{content:[{marks:[{attrs:{active:true},type:'bold'}],text:'same',type:'text'}],attrs:{level:1,align:null},type:'paragraph'}],type:'doc'}
  const beforeA=JSON.stringify(a),beforeB=JSON.stringify(b)
  assert.equal(equal(a,b),true);assert.equal(JSON.stringify(a),beforeA);assert.equal(JSON.stringify(b),beforeB)
  assert.equal(equal([1,2],[2,1]),false);assert.equal(equal({body:[a,b]},{body:[b,a]}),true)
  const changed=structuredClone(b);changed.content[0].content[0].text='different';assert.equal(equal(a,changed),false)
  for(const [a,b] of [[1,'1'],[false,0],[null,undefined],[{a:undefined},{}],[{a:null},{a:undefined}],[[undefined],[null]],[[1],{'0':1}]])assert.equal(equal(a,b),false)
  assert.equal(equal(undefined,undefined),true);assert.equal(equal(null,null),true)
})
test('sequential encrypted body-title-body operations synchronize through JSONB key reorder without a false conflict',async()=>{
  const {signIn}=await import('./tab-environment.mjs'),{syncQueue}=await import('../src/features/offline/sync.ts'),{repo}=await repository()
  const body=text=>({type:'doc',content:[{type:'paragraph',content:[{type:'text',text}]}]})
  const initial={id:other,revision:1,title:'old title',kind:'text',notebook_id:null,tag_ids:[],body_json:body('old'),plain_text:'old',deleted_at:null}
  await repo.mutate(w=>w.notes.push(structuredClone(initial)))
  const baseline=structuredClone(initial)
  await saveNoteDraft(repo,other,baseline,{...noteDraft(baseline),body_json:body('A first'),plain_text:'A first'})
  const aBaseline=structuredClone((await repo.value()).notes[0])
  await saveNoteDraft(repo,other,baseline,{...noteDraft(baseline),title:'B title'})
  await saveNoteDraft(repo,other,aBaseline,{...noteDraft(aBaseline),body_json:body('A second'),plain_text:'A second'})
  const queueBefore=structuredClone((await repo.value()).queue),savedFetch=globalThis.fetch,savedNavigator=Object.getOwnPropertyDescriptor(globalThis,'navigator'),requests=[]
  const reorder=value=>Array.isArray(value)?value.map(reorder):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,reorder(value[key])])):value
  let server=structuredClone(initial)
  signIn(owner,'j');Object.defineProperty(globalThis,'navigator',{configurable:true,value:{locks:{request:async(_name,_options,fn)=>fn({})}}})
  globalThis.fetch=async(path,init)=>{
    const reply=data=>new Response(JSON.stringify(data),{status:200,headers:{'content-type':'application/json'}})
    if(path==='/api/v1/auth/me')return reply({id:owner})
    if(path==='/api/v1/offline/snapshot')return reply({sync_epoch:epoch,notes:[server],pages:[]})
    if(path==='/api/v1/offline/sync'){
      const op=JSON.parse(init.body);requests.push(op);assert.equal(op.base_revision,server.revision)
      server={...server,...structuredClone(op.payload),revision:server.revision+1};server.body_json=reorder(server.body_json)
      return reply({operation_id:op.operation_id,kind:op.kind,resource_id:other,resource_mapping:{},applied_revision:server.revision,transport:{}})
    }
    throw new Error('unexpected path '+path)
  }
  try{
    await syncQueue(repo,()=>{});const result=await repo.value()
    assert.equal(result.queue.filter(q=>q.state!=='done').length,0);assert.equal(requests.length,3)
    assert.deepEqual(requests.map(op=>op.operation_id),queueBefore.map(q=>q.operation.operation_id))
    assert.deepEqual(requests.map(op=>op.payload),queueBefore.map(q=>q.operation.payload))
    assert.equal(server.plain_text,'A second');assert.equal(server.title,'B title');assert.equal(equal(server.body_json,body('A second')),true)
    assert.equal(result.notes[0].plain_text,'A second');assert.equal(result.notes[0].title,'B title')
  }finally{globalThis.fetch=savedFetch;if(savedNavigator)Object.defineProperty(globalThis,'navigator',savedNavigator);else delete globalThis.navigator}
})
