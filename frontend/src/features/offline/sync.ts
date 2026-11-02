import {settleDeletion} from './deletion.ts'
import {OfflineRepository} from './repository.ts'
import {OfflineHttpError,onlineContext,type OnlineContext} from './network.ts'
import {applyMappings,threeWay,unchangedResource} from './merge.ts'
import {noteForOperation,project,queueOperation} from './queue.ts'
import {assetId,uuid,type Current,type Entity,type Json,type QueueItem,type Snapshot,type SyncResult,type Workspace,type Operation} from './types.ts'

const epochMessage='服务器空间经过恢复或重建，同步代次已改变。已停止全部同步并保留本机修改；请导出本机内容，核对服务器后人工迁移。不能自动重放旧创建操作。'
function currentValue(current:Current):Json{return (current.note||current.folder||current.notebook||current.tag||{}) as Json}
export async function fresh(net:OnlineContext,q:QueueItem,w:Workspace):Promise<Current>{
  const noteId=noteForOperation(w,q)
  if(noteId){
    const s=await net.json<Snapshot>('/offline/snapshot','POST',{scope:'selected',note_ids:[noteId]})
    if(s.sync_epoch!==q.operation.sync_epoch)throw new OfflineHttpError(409,'offline_epoch_changed',epochMessage)
    return {resource_kind:'note',exists:s.notes.length===1,note:s.notes[0]||null,pages:s.pages}
  }
  const resource=q.operation.kind.split('.')[0]
  if(!['folder','notebook','tag'].includes(resource))return {resource_kind:resource,exists:true}
  try{
    const row=await net.json<Entity>(`/${resource==='folder'?'folders':resource==='notebook'?'notebooks':'tags'}/${q.operation.resource_id}`)
    return {resource_kind:resource,exists:true,[resource]:row}
  }catch(error){if(error instanceof OfflineHttpError&&error.status===404)return {resource_kind:resource,exists:false};throw error}
}
function localValue(q:QueueItem):Json{
  if(q.operation.kind==='page.order')return {...q.base,page_ids:q.operation.payload.page_ids}
  return {...q.base,...q.operation.payload}
}
function conflictFields(q:QueueItem,current:Current){
  const server={...currentValue(current),...(q.operation.kind.startsWith('page.')?{page_ids:(current.pages||[]).map(p=>p.id)}:{})}
  if(current.note?.deleted_at&&!q.base.deleted_at&&q.operation.kind!=='note.restore')return {server,merged:localValue(q),conflicts:['deleted_at']}
  const fields=Object.keys(q.operation.payload)
  if(q.operation.kind.endsWith('.update')||q.operation.kind==='page.order')return {server,...threeWay(q.base,localValue(q),server,fields)}
  return {server,merged:localValue(q),conflicts:unchangedResource(q.base,server)?[]:['resource']}
}
function reconcile(w:Workspace,current:Current,q:QueueItem){
  if(!current.exists){
    const resource=q.operation.kind.split('.')[0],id=q.operation.resource_id
    if(resource==='note'){w.notes=w.notes.filter(n=>n.id!==id);w.pages=w.pages.filter(p=>p.note_id!==id)}
    else if(resource==='page')w.pages=w.pages.filter(p=>p.id!==id)
    else if(['folder','notebook','tag'].includes(resource)){const key=resource==='folder'?'folders':resource==='notebook'?'notebooks':'tags';w[key]=w[key].filter(x=>x.id!==id)}
    return
  }
  const noteId=current.note?.id
  if(current.note){
    const index=w.notes.findIndex(n=>n.id===noteId);if(index>=0)w.notes[index]=structuredClone(current.note);else w.notes.push(structuredClone(current.note))
    const localPages=w.pages.filter(p=>p.note_id===noteId&&p.local)
    w.pages=w.pages.filter(p=>p.note_id!==noteId).concat(current.pages||[],localPages.filter(p=>!current.pages?.some(s=>s.id===p.id)))
    for(const pending of w.queue.filter(x=>x.state!=='done'&&noteForOperation(w,x)===noteId)){
      if(pending.operation.kind.startsWith('note.')&&!pending.operation.kind.endsWith('.create'))project(w,pending.operation.kind,noteId!,pending.operation.payload)
      if(pending.operation.kind==='page.remove')project(w,'page.remove',pending.operation.resource_id,pending.operation.payload)
      if(pending.operation.kind==='page.order'){
        const ids=pending.operation.payload.page_ids as string[];w.pages.filter(p=>p.note_id===noteId).forEach(p=>{const pos=ids.indexOf(p.id);if(pos>=0)p.position=pos})
      }
    }
  }else{
    const resource=q.operation.kind.split('.')[0],row=current.folder||current.notebook||current.tag
    if(row){const list=w[resource==='folder'?'folders':resource==='notebook'?'notebooks':'tags'],index=list.findIndex(x=>x.id===row.id);if(index>=0)list[index]=structuredClone(row);else list.push(structuredClone(row));for(const pending of w.queue.filter(x=>x.state!=='done'&&x.operation.resource_id===row.id))if(pending.operation.kind.endsWith('.update'))Object.assign(list.find(x=>x.id===row.id)!,pending.operation.payload)}
  }
}
async function conflict(repo:OfflineRepository,id:string,current:Current|null,message:string){
  await repo.mutate(w=>{
    const q=w.queue.find(x=>x.operation.operation_id===id)!;const found=current?conflictFields(q,current):{conflicts:['resource']}
    q.state='conflict';q.error=message;q.conflict={base:structuredClone(q.base),local:localValue(q),server:current,fields:found.conflicts.length?found.conflicts:['resource'],message}
  })
}
async function sendBytes(repo:OfflineRepository,net:OnlineContext,q:QueueItem){
  if(!q.assetKey||!q.transport?.upload_id)throw new Error('上传阶段缺少本机原件或服务器上传编号')
  const upload=await net.json<{id:string;status:string;upload_method:string;upload_url:string;file:{mime:string;status:string}}>(`/uploads/${q.transport.upload_id}`)
  net.check()
  if(['received','uploaded','completed','ready','processing'].includes(upload.status)||['ready','processing'].includes(upload.file.status))return
  if(['expired','rejected','failed'].includes(upload.status))throw new Error('服务器上传已过期或拒绝；请保留原件并在冲突/失败工作区核对，不能更换旧操作编号盲重试')
  const target=new URL(upload.upload_url,window.location.origin)
  if(target.origin!==window.location.origin||target.pathname!==`/api/v1/uploads/${upload.id}/content`||upload.upload_method!=='PUT')throw new Error('服务器返回了不受允许的上传地址')
  const blob=await repo.blob(q.assetKey,upload.file.mime);net.check()
  const response=await net.response(target.pathname.slice('/api/v1'.length)+target.search,{method:'PUT',headers:{'Content-Type':blob.type},body:blob})
  net.check();if(!response.ok)throw new Error(`原件传输失败（${response.status}）；重试会读取原上传状态`)
}
async function run(repo:OfflineRepository,progress:(message:string)=>void,leaseCheck:()=>Promise<void>){
  const net=await onlineContext(repo.cap)
  try{
    let count=0
    while(true){
      await leaseCheck();net.check();const w=await repo.value()
      if(w.syncBlocked)throw new Error(w.syncBlocked)
      const next=w.queue.find(q=>['pending','sending'].includes(q.state)&&q.depends.every(dep=>w.queue.find(x=>x.operation.operation_id===dep)?.state==='done'))
      if(!next){progress(w.queue.some(x=>x.state==='conflict')?'同步已暂停：请处理冲突后继续':'本机队列已同步；联网转换中的 PDF 仍需完成后重新缓存');return}
      const id=next.operation.operation_id
      try{
        if(!next.sent&&!next.operation.kind.endsWith('.create')){
          const current=await fresh(net,next,w);net.check();const value=currentValue(current)
          if(!current.exists){await conflict(repo,id,current,'资源已不存在，请核对并选择服务器版本或保留真实副本');continue}
          const revision=Number(value.revision)
          if(revision&&revision!==next.operation.base_revision){
            const merged=['upload.complete','pdf.start'].includes(next.operation.kind)&&!current.note?.deleted_at?{server:value,merged:localValue(next),conflicts:[]}:conflictFields(next,current)
            if(merged.conflicts.length){await conflict(repo,id,current,'服务器与本机同时修改了相同内容');continue}
            await repo.mutate(state=>{const q=state.queue.find(x=>x.operation.operation_id===id)!;if(q.sent)throw new Error('操作已发送，不能改写');const payload:Json={};for(const key of Object.keys(q.operation.payload))payload[key]=merged.merged[key];q.operation={...q.operation,base_revision:revision,payload};q.base=merged.server})
          }
        }
        const prepared=(await repo.value()).queue.find(x=>x.operation.operation_id===id)!
        if(prepared.operation.kind==='upload.complete'||prepared.operation.kind==='pdf.start')await sendBytes(repo,net,prepared)
        await leaseCheck();net.check()
        // Mark sent durably before the HTTP mutation; payload and UUID are immutable afterwards.
        const frozen=await repo.mutate(state=>{const q=state.queue.find(x=>x.operation.operation_id===id)!;q.state='sending';q.sent=true;delete q.error;return structuredClone(q.operation)})
        net.check();progress(`正在同步 ${++count} · ${frozen.kind}`)
        const result=await net.json<SyncResult>('/offline/sync','POST',frozen);net.check()
        if(result.operation_id!==id)throw new Error('服务器操作回执不匹配')
        // A replay receipt's revision is historical. Read the actual current state before rebasing dependants.
        const mappedQ={...prepared,operation:{...frozen,resource_id:result.resource_id},noteId:result.transport.note_id||prepared.noteId,transport:result.transport}
        let current:Current|null=null
        if(!frozen.kind.endsWith('.delete')){
          try{current=await fresh(net,mappedQ,w)}catch(error){if(error instanceof OfflineHttpError&&error.code==='offline_epoch_changed')throw error;throw new Error('操作已提交但读取当前版本失败；保留发送状态，重试将使用同一操作编号')}
        }
        net.check();await repo.mutate(state=>{
          const q=state.queue.find(x=>x.operation.operation_id===id)!
          const mapping={...result.resource_mapping}
          if(q.localFileId&&result.transport.file_id)mapping[q.localFileId]=result.transport.file_id
          if(q.localPageId&&result.transport.page_id)mapping[q.localPageId]=result.transport.page_id
          applyMappings(state,mapping)
          q.state='done';q.transport=result.transport
          const continuation=state.queue.find(x=>x.operation.operation_id===q.continuation)
          if(continuation&&!continuation.sent){continuation.transport=result.transport;continuation.noteId=result.transport.note_id||continuation.noteId;continuation.operation={...continuation.operation,resource_id:(frozen.kind==='pdf.create'?result.transport.import_id:result.transport.upload_id)||continuation.operation.resource_id};if(current?.note)continuation.operation.base_revision=current.note.revision}
          if(frozen.kind==='pdf.create'){const pdf=state.pdf_imports.find(p=>p.note_id===result.transport.note_id);if(pdf){pdf.id=result.transport.import_id||pdf.id;pdf.status='awaiting_upload'}}
          if(frozen.kind==='pdf.start'){const pdf=state.pdf_imports.find(p=>p.id===frozen.resource_id);if(pdf)pdf.status='queued'}
          if(frozen.kind==='upload.complete'){const page=state.pages.find(p=>p.id===result.transport.page_id);if(page)page.local=false}
          if(current)reconcile(state,current,q)
        })
      }catch(error){
        if(error instanceof OfflineHttpError&&error.code==='offline_epoch_changed'){await repo.mutate(state=>{state.syncBlocked=epochMessage});throw new Error(epochMessage)}
        if(error instanceof OfflineHttpError&&(error.status===409||error.status===404)){
          const latest=(await repo.value()).queue.find(x=>x.operation.operation_id===id)!
          if(error.status===409&&error.code==='revision_conflict'&&error.current&&['note.update','folder.update','notebook.update','tag.update','page.order'].includes(latest.operation.kind)){
            const merge=conflictFields(latest,error.current)
            if(!merge.conflicts.length){await resolveConflictAuto(repo,id,error.current,merge.merged);continue}
          }
          const current=error.current||(error.status===404?await fresh(net,latest,await repo.value()):null)
          await conflict(repo,id,current,error.message);continue
        }
        repo.cap.check();await repo.mutate(state=>{const q=state.queue.find(x=>x.operation.operation_id===id)!;q.error=error instanceof Error?error.message:'网络中断';if(!q.sent)q.state='pending'})
        throw error
      }
    }
  }finally{net.close()}
}
async function resolveConflictAuto(repo:OfflineRepository,id:string,current:Current,payload:Json){
  await repo.mutate(w=>{
    const old=w.queue.find(q=>q.operation.operation_id===id)!,server=currentValue(current),selected:Json={}
    for(const key of Object.keys(old.operation.payload))selected[key]=payload[key]
    old.state='done';const replacement=queueOperation(w,old.operation.kind,old.operation.resource_id,selected,server,{noteId:old.noteId,assetKey:old.assetKey,transport:old.transport,localFileId:old.localFileId,localPageId:old.localPageId})
    replacement.depends=old.depends
    for(const dependent of w.queue)if(dependent!==replacement&&dependent.depends.includes(id))dependent.depends=dependent.depends.map(dep=>dep===id?replacement.operation.operation_id:dep)
    reconcile(w,current,old)
  })
}
export async function resolveConflict(repo:OfflineRepository,id:string,choice:'server'|'local'|'manual'|'copy',manual?:Json){
  const w=await repo.value(),q=w.queue.find(x=>x.operation.operation_id===id)
  if(!q?.conflict)throw new Error('冲突已不存在')
  const current=q.conflict.server
  if(current?.exists===false&&['server','copy'].includes(choice)){await repo.mutate(state=>{const item=state.queue.find(x=>x.operation.operation_id===id)!;settleDeletion(state,item,choice as 'server'|'copy')});return}
  if(choice==='copy')throw new Error('只有服务器已删除的组织支持恢复组织副本')
  if(choice==='server'){
    await repo.mutate(state=>{const item=state.queue.find(x=>x.operation.operation_id===id)!;item.state='done';if(current)reconcile(state,current,item);else throw new Error('服务器没有安全的当前版本；请保留副本或导出核对')});return
  }
  if(!current?.exists)throw new Error('资源已删除，不能静默恢复；请使用保留副本或明确回收站恢复')
  if(current.note?.deleted_at&&!q.base.deleted_at&&q.operation.kind!=='note.restore')throw new Error('服务器笔记已进入回收站；请先保留副本，或明确选择恢复后重新编辑')
  const desired=choice==='manual'?manual:q.conflict.local
  if(!desired)throw new Error('缺少手工解决内容')
  await resolveConflictAuto(repo,id,current,desired)
}
export async function syncQueue(repo:OfflineRepository,progress:(message:string)=>void){
  repo.cap.check();const name=`ai-notes:offline-sync:${repo.cap.owner}`
  if(navigator.locks){return navigator.locks.request(name,{mode:'exclusive',ifAvailable:true},async lock=>{if(!lock)throw new Error('另一个标签页正在同步，请稍后重试');await run(repo,progress,async()=>{repo.cap.check()})})}
  const holder=uuid();if(!await repo.db.lease(repo.cap.owner,holder,'acquire'))throw new Error('另一个标签页正在同步，请稍后重试')
  let valid=true
  const check=async()=>{repo.cap.check();if(!valid||!await repo.db.lease(repo.cap.owner,holder,'renew')){valid=false;throw new Error('同步租约已转移，已停止本标签页')}}
  const timer=setInterval(()=>{void repo.db.lease(repo.cap.owner,holder,'renew').then(ok=>{valid=valid&&ok}).catch(()=>{valid=false})},4000)
  try{return await run(repo,progress,check)}finally{clearInterval(timer);await repo.db.lease(repo.cap.owner,holder,'release')}
}
