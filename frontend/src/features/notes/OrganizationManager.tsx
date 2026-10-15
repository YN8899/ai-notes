import { useState } from 'react'
import { api } from '../../api'
import { NoteError } from './NoteError'
import type { Folder,Notebook,Tag } from './types'
type Kind='folders'|'notebooks'|'tags'
export function OrganizationManager({folders,notebooks,tags,onChange}:{folders:Folder[];notebooks:Notebook[];tags:Tag[];onChange:()=>void}) {
  const [kind,setKind]=useState<Kind>('folders'),[id,setId]=useState(''),[name,setName]=useState(''),[parent,setParent]=useState('')
  const [busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null)
  const entities=kind==='folders'?folders:kind==='notebooks'?notebooks:tags
  const selected=entities.find(x=>x.id===id)
  const invalid=new Set<string>(id?[id]:[])
  if(kind==='folders'&&id){let changed=true;while(changed){changed=false;for(const f of folders)if(f.parent_id&&invalid.has(f.parent_id)&&!invalid.has(f.id)){invalid.add(f.id);changed=true}}}
  function reset(next:Kind=kind){setKind(next);setId('');setName('');setParent('');setError(null)}
  function select(value:string){setId(value);const e=entities.find(x=>x.id===value);setName(e?.name||'');setParent(kind==='folders'?folders.find(f=>f.id===value)?.parent_id||'':kind==='notebooks'?notebooks.find(n=>n.id===value)?.folder_id||'':'');setError(null)}
  async function save(remove=false){
    if(remove&&!window.confirm('删除该项目？服务端会检查其关联内容；无法删除的原因会显示在此处。'))return
    setBusy(true);setError(null)
    try {const body=remove?{revision:selected!.revision}:{name:name.trim(),...(kind==='folders'?{parent_id:parent||null}:kind==='notebooks'?{folder_id:parent||null}:{}),...(selected?{revision:selected.revision}:{})};await api('/'+kind+(id?'/'+id:''),remove?'DELETE':id?'PATCH':'POST',body);reset();onChange()}catch(e){setError(e);onChange()}finally{setBusy(false)}
  }
  return <details className="organization panel"><summary>管理文件夹、笔记本与标签</summary><div className="organization-form"><label>管理类型<select value={kind} onChange={e=>reset(e.target.value as Kind)}><option value="folders">文件夹</option><option value="notebooks">笔记本</option><option value="tags">标签</option></select></label><label>管理项目<select value={id} onChange={e=>select(e.target.value)}><option value="">新建项目</option>{entities.map(x=><option value={x.id} key={x.id}>{x.name}</option>)}</select></label><label>项目名称<input value={name} maxLength={kind==='tags'?64:120} onChange={e=>setName(e.target.value)} placeholder="输入名称"/></label>{kind!=='tags'?<label>{kind==='folders'?'上级文件夹':'所属文件夹'}<select value={parent} onChange={e=>setParent(e.target.value)}><option value="">根目录</option>{folders.filter(f=>!invalid.has(f.id)).map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label>:null}<div className="note-actions"><button className="primary" disabled={busy||!name.trim()} onClick={()=>save()}>{id?'保存项目':'创建项目'}</button>{id?<button className="secondary" disabled={busy} onClick={()=>save(true)}>删除项目</button>:null}<button className="text-button" disabled={busy} onClick={()=>reset()}>重置表单</button></div></div><NoteError error={error}/><p className="muted">文件夹可嵌套。移动时会排除自身和下级目录，避免形成循环。</p></details>
}
