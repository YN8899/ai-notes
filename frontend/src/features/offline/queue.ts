import {checkExpected,changedNoteFields,noteDraft,type NoteDraft} from './draft.ts'
import {validateImage,validatePdf} from './file-validation.ts'
import {OfflineRepository} from './repository.ts'
import {assetId,uuid,type Workspace,type Kind,type Json,type QueueItem,type Note,type Page,type Operation} from './types.ts'
export function validatePayload(kind:Kind,payload:Json){
  const nameLimit=kind.startsWith('tag.')?64:kind.startsWith('note.')?200:120
  for(const field of ['title','name'])if(field in payload&&(typeof payload[field]!=='string'||!(payload[field] as string).trim()||(payload[field] as string).length>nameLimit))throw new Error('标题或组织名称无效')
  if('plain_text'in payload&&(typeof payload.plain_text!=='string'||payload.plain_text.length>1_000_000))throw new Error('正文纯文本无效或过长')
  if('body_json'in payload){const body=payload.body_json;if(!body||typeof body!=='object'||Array.isArray(body)||Object.keys(body).length&&((body as Json).type!=='doc'||!Array.isArray((body as Json).content)))throw new Error('富文本 JSON 必须是完整 doc 文档')}
  for(const key of ['tag_ids','page_ids'])if(key in payload&&(!Array.isArray(payload[key])||(payload[key] as unknown[]).some(x=>typeof x!=='string')||new Set(payload[key] as string[]).size!==(payload[key] as string[]).length))throw new Error('标签或页序标识无效')
  if(kind==='page.order'&&(!(payload.page_ids as unknown[])?.length||(payload.page_ids as unknown[]).length>500))throw new Error('页序须包含 1–500 个页面')
  if(kind.endsWith('.update')&&!Object.keys(payload).length)throw new Error('至少需要修改一个字段')
  const json=JSON.stringify(payload)
  if(json.includes('\\u0000')||new TextEncoder().encode(json).length>1_048_576)throw new Error('内容包含无效字符或超过 1 MiB')
}
export function noteForOperation(w:Workspace,q:QueueItem){
  return q.noteId||q.transport?.note_id||(q.operation.kind.startsWith('note.')||q.operation.kind==='page.order'?q.operation.resource_id:typeof q.operation.payload.note_id==='string'?q.operation.payload.note_id:undefined)
}
export function queueOperation(w:Workspace,kind:Kind,resourceId:string,payload:Json,base:Json={},extra:Partial<QueueItem>={}):QueueItem{
  if(!w.syncEpoch)throw new Error('离线库尚未取得服务器同步代次，请先联网完成一次资料缓存')
  validatePayload(kind,payload)
  const op:Operation={operation_id:uuid(),sync_epoch:w.syncEpoch,kind,resource_id:resourceId,base_revision:kind.endsWith('.create')?null:Number(base.revision)||1,payload:structuredClone(payload)}
  const refs=new Set([resourceId,...['note_id','parent_id','folder_id','notebook_id'].flatMap(k=>typeof payload[k]==='string'?[payload[k] as string]:[]),...(Array.isArray(payload.tag_ids)?payload.tag_ids as string[]:[])])
  const deps=w.queue.filter(q=>q.state!=='done'&&(refs.has(q.operation.resource_id)||(extra.noteId&&noteForOperation(w,q)===extra.noteId)))
  const q:QueueItem={operation:op,depends:deps.map(q=>q.operation.operation_id),base:structuredClone(base),state:'pending',sent:false,...extra}
  w.queue.push(q);return q
}
export function project(w:Workspace,kind:Kind,id:string,payload:Json){
  const [resource,action]=kind.split('.')
  if(['folder','notebook','tag'].includes(resource)){
    const list=w[resource==='folder'?'folders':resource==='notebook'?'notebooks':'tags']
    if(action==='create')list.push({id,revision:1,name:String(payload.name),...payload})
    else if(action==='delete'){
      const index=list.findIndex(x=>x.id===id);if(index>=0)list.splice(index,1)
      if(resource==='folder'){w.folders.forEach(x=>{if(x.parent_id===id)x.parent_id=null});w.notebooks.forEach(x=>{if(x.folder_id===id)x.folder_id=null})}
      if(resource==='notebook')w.notes.forEach(x=>{if(x.notebook_id===id)x.notebook_id=null})
      if(resource==='tag')w.notes.forEach(x=>{x.tag_ids=x.tag_ids.filter(t=>t!==id)})
    }else {const row=list.find(x=>x.id===id);if(!row)throw new Error('组织不存在');Object.assign(row,payload)}
  }else if(resource==='note'){
    if(action==='create'){w.notes.push({id,revision:1,title:'新笔记',kind:'text',notebook_id:null,tag_ids:[],body_json:{},plain_text:'',deleted_at:null,...payload} as Note)}
    else{const note=w.notes.find(x=>x.id===id);if(!note)throw new Error('笔记不在本机离线库');if(action==='update')Object.assign(note,payload);else note.deleted_at=action==='trash'?new Date().toISOString():null}
  }else if(kind==='page.order'){
    const ids=payload.page_ids as string[]
    const pages=w.pages.filter(x=>x.note_id===id)
    if(ids.length!==pages.length||new Set(ids).size!==ids.length||pages.some(p=>!ids.includes(p.id)))throw new Error('必须包含当前全部页面')
    for(const p of pages)p.position=ids.indexOf(p.id)
  }else if(kind==='page.remove')w.pages=w.pages.filter(p=>p.id!==id)
}
export async function edit(repo:OfflineRepository,kind:Kind,id:string,payload:Json,expected?:Json){
  return repo.mutate(w=>{
    const [resource]=kind.split('.')
    const noteId=kind==='page.remove'?String(payload.note_id):kind==='page.order'||resource==='note'?id:undefined
    const base=resource==='note'||resource==='page'?w.notes.find(x=>x.id===noteId):w[resource==='folder'?'folders':resource==='notebook'?'notebooks':'tags'].find(x=>x.id===id)
    const fullBase={...base,...(resource==='page'?{page_ids:w.pages.filter(p=>p.note_id===noteId).sort((a,b)=>a.position-b.position).map(p=>p.id)}:{})}
    if(expected)checkExpected(fullBase,expected,payload)
    if(kind==='note.update'&&('body_json'in payload||'plain_text'in payload))w.history.push({note_id:id,value:{body_json:base&&'body_json'in base?base.body_json:{},plain_text:base&&'plain_text'in base?base.plain_text:''},at:new Date().toISOString()})
    project(w,kind,id,payload);queueOperation(w,kind,id,payload,fullBase,{noteId});return id
  })
}
function attachmentOperations(w:Workspace,kind:'upload.create'|'pdf.create',id:string,payload:Json,extra:Partial<QueueItem>){
  const create=queueOperation(w,kind,id,payload,{},extra)
  const finish=queueOperation(w,kind==='upload.create'?'upload.complete':'pdf.start',id,{}, {revision:1},extra)
  finish.depends=[...new Set([...finish.depends,create.operation.operation_id])]
  create.continuation=finish.operation.operation_id
}
export async function addImage(repo:OfflineRepository,noteId:string,file:Blob,filename:string){
  repo.cap.check();await validateImage(file);repo.cap.check()
  const key=uuid(),localFileId=uuid(),localPageId=uuid(),uploadId=uuid()
  await repo.mutate(w=>{
    const note=w.notes.find(n=>n.id===noteId);if(!note||note.kind!=='image'||note.deleted_at)throw new Error('请先选择有效的图片笔记')
    w.assets[assetId(localFileId)]=key;w.files.push({id:localFileId,filename,mime:file.type,bytes:file.size,status:'local'})
    w.pages.push({id:localPageId,file_id:localFileId,note_id:noteId,position:w.pages.filter(p=>p.note_id===noteId).length,revision:1,local:true})
    attachmentOperations(w,'upload.create',uploadId,{note_id:noteId,filename,mime:file.type,size:file.size},{assetKey:key,noteId,localFileId,localPageId})
  },[{key,blob:file}])
}
export async function addPdf(repo:OfflineRepository,file:Blob,filename:string,notebookId:string|null=null){
  repo.cap.check();const pageCount=await validatePdf(file);repo.cap.check()
  const id=uuid(),key=uuid(),localFileId=uuid()
  await repo.mutate(w=>{
    project(w,'note.create',id,{title:filename,kind:'image',notebook_id:notebookId})
    w.assets[assetId(localFileId)]=key;w.files.push({id:localFileId,filename,mime:file.type,bytes:file.size,status:'local'})
    w.pdf_imports.push({id,note_id:id,source_file_id:localFileId,status:'local_pending',page_count:pageCount,completed_pages:0})
    attachmentOperations(w,'pdf.create',id,{filename,size:file.size,notebook_id:notebookId,tag_ids:[]},{assetKey:key,noteId:id,localFileId})
  },[{key,blob:file}]);return id
}
export async function copyNote(repo:OfflineRepository,noteId:string){
  const w=await repo.value(),note=w.notes.find(n=>n.id===noteId);if(!note)throw new Error('本机笔记不存在')
  const pages=w.pages.filter(p=>p.note_id===noteId).sort((a,b)=>a.position-b.position),images:{blob:Blob;filename:string}[]=[]
  if(note.kind==='image'){
    if(!pages.length)throw new Error('图片副本需要真实页面；PDF 请先完成联网转换或下载原件保存')
    for(const page of pages){const file=w.files.find(f=>f.id===page.file_id),key=w.assets[assetId(page.file_id)];if(!file||!key)throw new Error('原图未完整缓存，不能创建缺失页面的副本');images.push({blob:await repo.blob(key,file.mime),filename:file.filename})}
  }
  const id=uuid(),payload={title:note.title+'（本机副本）',kind:note.kind,notebook_id:note.notebook_id,tag_ids:note.tag_ids,body_json:note.body_json,plain_text:note.plain_text}
  // Build all encrypted blobs and local projection in one atomic commit.
  const attachments=images.map(image=>({...image,key:uuid(),fileId:uuid(),pageId:uuid(),uploadId:uuid()}))
  await repo.mutate(next=>{
    project(next,'note.create',id,payload);queueOperation(next,'note.create',id,payload)
    attachments.forEach((a,index)=>{next.files.push({id:a.fileId,filename:a.filename,mime:a.blob.type,bytes:a.blob.size,status:'local'});next.assets[assetId(a.fileId)]=a.key;next.pages.push({id:a.pageId,note_id:id,file_id:a.fileId,position:index,revision:1,local:true});attachmentOperations(next,'upload.create',a.uploadId,{note_id:id,filename:a.filename,mime:a.blob.type,size:a.blob.size},{noteId:id,assetKey:a.key,localFileId:a.fileId,localPageId:a.pageId})})
  },attachments.map(a=>({key:a.key,blob:a.blob})));return id
}
export function orderedPages(w:Workspace,noteId:string):Page[]{return w.pages.filter(p=>p.note_id===noteId).sort((a,b)=>a.position-b.position)}



export async function saveNoteDraft(repo:OfflineRepository,id:string,expected:Note,draft:NoteDraft){
  const patch=changedNoteFields(noteDraft(expected),{...draft,title:draft.title.trim()})
  if(Object.keys(patch).length)await edit(repo,'note.update',id,patch,expected as unknown as Json)
  const current=(await repo.value()).notes.find(note=>note.id===id)
  if(!current)throw new Error('本机笔记已移除，界面草稿仍保留，请先复制保存')
  return current
}
