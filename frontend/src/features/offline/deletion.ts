import {project,queueOperation,noteForOperation} from './queue.ts'
import {applyMappings} from './merge.ts'
import {uuid,type QueueItem,type Workspace,type Json} from './types.ts'
const organizations=['folder','notebook','tag']
export function affectedByDeletion(w:Workspace,item:QueueItem){
  const ids=new Set([item.operation.operation_id]),resource=item.operation.resource_id,kind=item.operation.kind.split('.')[0],noteId=noteForOperation(w,item)
  for(const q of w.queue)if(q.state!=='done'&&(q.operation.resource_id===resource||noteId&&noteForOperation(w,q)===noteId))ids.add(q.operation.operation_id)
  let changed=true
  while(changed){changed=false;for(const q of w.queue)if(q.state!=='done'&&!ids.has(q.operation.operation_id)&&q.depends.some(id=>ids.has(id))){ids.add(q.operation.operation_id);changed=true}}
  return w.queue.filter(q=>q.state!=='done'&&ids.has(q.operation.operation_id))
}
function withoutReference(payload:Json,id:string){
  const next=structuredClone(payload)
  for(const key of ['parent_id','folder_id','notebook_id'])if(next[key]===id)next[key]=null
  if(Array.isArray(next.tag_ids))next.tag_ids=next.tag_ids.filter(value=>value!==id)
  return next
}
export function settleDeletion(w:Workspace,item:QueueItem,choice:'server'|'copy'){
  if(item.conflict?.server?.exists!==false)throw new Error('必须先取得服务器明确的资源不存在状态')
  const kind=item.operation.kind.split('.')[0],id=item.operation.resource_id,affected=affectedByDeletion(w,item)
  if(choice==='copy'&&!organizations.includes(kind))throw new Error('笔记请先使用“保留真实副本”，再采用服务器删除')
  if(choice==='copy'&&affected.some(q=>q.sent&&q!==item))throw new Error('有关联操作已经发送，不能改写身份；请先核对该操作回执或保存原件')
  const now=new Date().toISOString(),noteIds=new Set(affected.flatMap(q=>{const note=noteForOperation(w,q);return note?[note]:[]}))
  // Retain the full original projection and queue before any explicit deletion resolution.
  w.history.push({note_id:id,at:now,value:{reason:choice==='copy'?'server_deleted_copy':'server_deleted_accepted',resource_kind:kind,affected:structuredClone(affected),files:structuredClone(w.files),assets:structuredClone(w.assets),folders:structuredClone(w.folders),notebooks:structuredClone(w.notebooks),tags:structuredClone(w.tags),notes:structuredClone(w.notes.filter(n=>noteIds.has(n.id))),pages:structuredClone(w.pages.filter(p=>noteIds.has(p.note_id))),pdf_imports:structuredClone(w.pdf_imports.filter(p=>noteIds.has(p.note_id)))}})
  for(const noteId of noteIds){const note=w.notes.find(n=>n.id===noteId);if(note)w.history.push({note_id:noteId,at:now,value:{reason:'server_deleted_dependency',note:structuredClone(note),operations:structuredClone(affected.filter(q=>noteForOperation(w,q)===noteId))}})}
  if(organizations.includes(kind)){
    const list=w[kind==='folder'?'folders':kind==='notebook'?'notebooks':'tags']
    const row=list.find(row=>row.id===id)||item.conflict.local
    for(const q of affected)if(q.operation.resource_id===id){q.state='done';q.error=choice==='copy'?'已明确恢复为新组织副本，原操作未重放':'已明确采用服务器删除，原操作未重放'}
    if(choice==='copy'){
      const newId=uuid(),payload:Json={name:String(row.name||'恢复的组织')}
      if(kind==='folder')payload.parent_id=row.parent_id||null
      if(kind==='notebook')payload.folder_id=row.folder_id||null
      if(!list.some(row=>row.id===id))list.push({id,revision:1,name:String(payload.name),...payload})
      applyMappings(w,{[id]:newId})
      const restored=list.find(row=>row.id===newId)!;restored.revision=1
      const create=queueOperation(w,`${kind}.create` as 'folder.create'|'notebook.create'|'tag.create',newId,payload)
      for(const q of affected)if(q.state!=='done')q.depends=[...new Set([...q.depends,create.operation.operation_id])]
      return newId
    }
    project(w,`${kind}.delete` as 'folder.delete'|'notebook.delete'|'tag.delete',id,{})
    // Keep unrelated edits and new notes; detach only the explicitly deleted relationship.
    for(const q of affected)if(q.state!=='done'&&!q.sent)q.operation={...q.operation,payload:withoutReference(q.operation.payload,id)}
  }else{
    // Whole-note absence ends its dependent operations only after explicit confirmation.
    for(const q of affected){q.state='done';q.error='已明确采用服务器删除，完整本机投影及操作保留在历史'}
    const noteId=noteForOperation(w,item)||id
    w.notes=w.notes.filter(n=>n.id!==noteId);w.pages=w.pages.filter(p=>p.note_id!==noteId);w.pdf_imports=w.pdf_imports.filter(p=>p.note_id!==noteId)
  }
}
