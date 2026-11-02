import {useState} from 'react'
import type {Json,QueueItem} from './types.ts'
import {OfflineRepository} from './repository.ts'
import {copyNote} from './queue.ts'
import {affectedByDeletion} from './deletion.ts'
import {resolveConflict} from './sync.ts'
function ConflictItem({item,repo,onChange,onError}:{item:QueueItem;repo:OfflineRepository;onChange:()=>Promise<void>;onError:(error:unknown)=>void}){
  const conflict=item.conflict!,[manual,setManual]=useState(JSON.stringify(conflict.local,null,2)),[busy,setBusy]=useState(false),[choices,setChoices]=useState<Record<string,'local'|'server'>>({})
  async function work(fn:()=>Promise<unknown>){setBusy(true);try{await fn();await onChange()}catch(error){onError(error)}finally{setBusy(false)}}
  const server=(conflict.server?.note||conflict.server?.folder||conflict.server?.notebook||conflict.server?.tag||{}) as Json
  const deleted=conflict.server?.exists===false,organization=['folder','notebook','tag'].includes(item.operation.kind.split('.')[0])
  async function adoptDeleted(copy=false){
    const value=await repo.value(),count=affectedByDeletion(value,item).length
    const message=copy?`明确恢复为新组织副本？将创建新编号，重定向 ${count} 项关联本机操作；不会复活服务器已删除的原组织。原投影和队列保留在本机历史。`:`采用服务器删除？涉及 ${count} 项本机操作。${organization?'该组织的操作将终止；关联笔记、正文和其他操作继续保留，解除已删除的所属/标签关系。':'该笔记及依赖操作将从当前资料和队列移除。可先保留真实副本。'}完整原投影和队列保留在本机删除处理历史，原件保留。`
    if(window.confirm(message))await resolveConflict(repo,item.operation.operation_id,copy?'copy':'server')
  }
  function selected():Json{
    const value={...conflict.local}
    for(const field of conflict.fields){if(!choices[field])throw new Error('请为每个冲突字段选择版本');if(choices[field]==='server'){for(const key of field==='body'?['body_json','plain_text']:field==='resource'?Object.keys(item.operation.payload):[field])value[key]=field==='page_ids'?conflict.server?.pages?.map(p=>p.id):server[key]}}
    return value
  }
  return <article className="offline-conflict"><h4>{item.operation.kind} · {item.operation.resource_id.slice(0,8)}</h4><p>{conflict.message}</p><p className="muted">本机、基线、服务器三个版本均保留。正文 JSON 与纯文本作为一个原子字段解决，页面顺序作为整体解决。</p>
    {!deleted?<div className="table-wrap"><table><thead><tr><th>冲突字段</th><th>选择版本</th></tr></thead><tbody>{conflict.fields.map(field=><tr key={field}><td>{field==='body'?'完整正文':field}</td><td><select value={choices[field]||''} onChange={e=>setChoices(old=>({...old,[field]:e.target.value as 'local'|'server'}))}><option value="">请选择</option><option value="local">本机</option><option value="server">服务器</option></select></td></tr>)}</tbody></table></div>:<p role="status">已核验服务器不存在该资源。普通编辑不能恢复原身份，请明确采用删除或保存副本。</p>}
    <details><summary>比较基线、本机、服务器</summary><div className="offline-versions"><pre>基线：{JSON.stringify(conflict.base,null,2)}</pre><pre>本机：{JSON.stringify(conflict.local,null,2)}</pre><pre>服务器：{JSON.stringify(conflict.server,null,2)}</pre></div></details>
    <div className="note-actions">{deleted?<><button className="secondary" disabled={busy} onClick={()=>void work(()=>adoptDeleted())}>明确采用服务器删除</button>{organization?<button className="secondary" disabled={busy} onClick={()=>void work(()=>adoptDeleted(true))}>明确恢复为新组织副本</button>:null}</>:<><button className="secondary" disabled={busy} onClick={()=>void work(()=>resolveConflict(repo,item.operation.operation_id,'manual',selected()))}>按逐项选择生成新操作</button><button className="secondary" disabled={busy} onClick={()=>void work(()=>resolveConflict(repo,item.operation.operation_id,'local'))}>使用本机版本生成新操作</button><button className="secondary" disabled={busy} onClick={()=>void work(()=>resolveConflict(repo,item.operation.operation_id,'server'))}>采用服务器版本</button></>}
      {item.noteId||item.operation.kind.startsWith('note.')?<button className="secondary" disabled={busy} onClick={()=>void work(async()=>{if(deleted){const value=await repo.value();if(!window.confirm(`创建真实副本并采用服务器删除？原笔记的 ${affectedByDeletion(value,item).length} 项关联操作将终止，历史和原件继续保留。`))return}await copyNote(repo,item.noteId||item.operation.resource_id);await resolveConflict(repo,item.operation.operation_id,'server')})}>保留真实副本，再采用服务器版本</button>:null}</div>
    {!deleted?<details><summary>手工编辑解决内容</summary><label>完整本机字段 JSON<textarea rows={12} value={manual} onChange={e=>setManual(e.target.value)}/></label><button className="secondary" disabled={busy} onClick={()=>void work(()=>resolveConflict(repo,item.operation.operation_id,'manual',JSON.parse(manual) as Json))}>核对后生成新操作</button></details>:null}
  </article>
}
export default function OfflineConflicts({items,repo,onChange,onError}:{items:QueueItem[];repo:OfflineRepository;onChange:()=>Promise<void>;onError:(error:unknown)=>void}){
  return items.length?<section className="panel"><h3>需要核对的同步冲突（{items.length}）</h3>{items.map(item=><ConflictItem key={item.operation.operation_id} item={item} repo={repo} onChange={onChange} onError={onError}/>)}</section>:null
}
