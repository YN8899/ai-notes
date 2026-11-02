import {useCallback,useEffect,useMemo,useState} from 'react'
import type {VaultCapability,Workspace} from './types.ts'
import {unfinished,uuid} from './types.ts'
import {OfflineRepository} from './repository.ts'
import {copyNote,edit} from './queue.ts'
import {clearVault,lockVault,revokeVaultUrl,vaultUrl} from './vault.ts'
import {syncQueue} from './sync.ts'
import OfflineOrganizations from './OfflineOrganizations.tsx'
import OfflineNote from './OfflineNote.tsx'
import OfflineConflicts from './OfflineConflicts.tsx'
import {OfflinePdfPicker} from './OfflineAssets.tsx'
import '../notes/notes.css'
import './offline.css'
export default function OfflineWorkspace({cap,onClose}:{cap:VaultCapability;onClose:()=>void}){
  const repo=useMemo(()=>new OfflineRepository(cap),[cap]),[value,setValue]=useState<Workspace|null>(null),[selected,setSelected]=useState(''),[query,setQuery]=useState(''),[trash,setTrash]=useState(false),[notebook,setNotebook]=useState(''),[folder,setFolder]=useState(''),[tag,setTag]=useState(''),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[dirty,setDirty]=useState(false),[title,setTitle]=useState(''),[kind,setKind]=useState<'text'|'image'>('text')
  const report=useCallback((e:unknown)=>setError(e instanceof Error?e.message:'离线操作失败'),[])
  const refresh=useCallback(async()=>{const next=await repo.value();cap.check();setValue(next)},[repo,cap])
  useEffect(()=>{void refresh().catch(report)},[refresh,report])
  useEffect(()=>{const before=(e:BeforeUnloadEvent)=>{if(dirty){e.preventDefault();e.returnValue=''}};window.addEventListener('beforeunload',before);return()=>window.removeEventListener('beforeunload',before)},[dirty])
  const navigate=(id:string)=>{if(dirty&&!window.confirm('当前界面有未保存修改，离开会丢失这些尚未进入加密存储的内容。继续？'))return;setDirty(false);setSelected(id)}
  async function work(fn:()=>Promise<unknown>){
    setBusy(true);setError('')
    try{await fn();await refresh()}
    catch(error){
      // A failed sync may already have durably committed progress or a stop flag.
      try{await refresh()}catch{/* Keep the last readable projection and the original operation error. */}
      try{cap.check();report(error)}catch{/* A retired vault must not publish a late error. */}
    }finally{setBusy(false)}
  }
  function lock(){if(dirty&&!window.confirm('界面有未保存修改。已加密保存的队列会保留；未保存内容会丢失。仍要锁定？'))return;lockVault()}
  async function exportLocal(){
    const state=await repo.value(),blob=new Blob([JSON.stringify(state,null,2)],{type:'application/json'}),url=vaultUrl(cap,blob)
    const link=document.createElement('a');link.href=url;link.download='本机离线笔记与队列.json';link.click();setTimeout(()=>revokeVaultUrl(url),5000)
    setMessage('已导出解密后的本机笔记与队列 JSON，文件不含认证凭据。原图和 PDF 请另用各笔记的原件下载按钮保存。请妥善保管。')
  }
  if(!value)return <main className="offline-entry"><section className="panel"><p>正在解密本机资料…</p>{error?<p className="error" role="alert">{error}</p>:null}<button className="secondary" onClick={onClose}>返回</button></section></main>
  const note=value.notes.find(n=>n.id===selected),count=unfinished(value).length
  const visible=value.notes.filter(n=>!!n.deleted_at===trash&&(!notebook||n.notebook_id===notebook)&&(!folder||value.notebooks.some(b=>b.id===n.notebook_id&&b.folder_id===folder))&&(!tag||n.tag_ids.includes(tag))&&(!query||`${n.title}\n${n.plain_text}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())))
  return <main className="offline-workspace notes-workspace"><section className="panel"><div className="panel-heading"><div><p className="eyebrow">本机离线空间</p><h1>我的离线笔记</h1><p className="muted">账号 {cap.owner.slice(0,8)} · 仅本机解锁，服务器身份须另外登录验证</p></div><div className="note-actions"><button className="secondary" disabled={busy} onClick={lock}>锁定并保留已保存修改</button><button className="secondary" disabled={busy} onClick={()=>{if(!dirty||window.confirm('界面尚有未保存内容，仍要离开？'))onClose()}}>返回在线或登录</button></div></div><p role="status">未同步 {count} 项 · {value.cache?`${(value.cache.bytes/1024/1024).toFixed(1)} MiB · ${value.cache.scope==='all'?'全量':'选择'}缓存 · ${new Date(value.cache.cachedAt).toLocaleString('zh-CN')}`:'尚无完整笔记快照'}</p>{value.syncBlocked?<p className="error" role="alert">{value.syncBlocked}</p>:null}<div className="note-actions"><button className="primary" disabled={busy||!!value.syncBlocked||dirty||!count} onClick={()=>void work(()=>syncQueue(repo,setMessage))}>验证服务器账号并同步</button><button className="secondary" disabled={busy} onClick={()=>void work(exportLocal)}>导出本机内容以便人工核对</button><button className="secondary" disabled={busy} onClick={()=>{if(window.confirm(`永久清除该账号本机离线资料？包含 ${count} 项未同步操作、全部密文和原件。此操作无法撤销；其他账号和服务器资料不受影响。`))void work(()=>clearVault(cap.owner,count))}}>清除本机离线资料</button></div><p className="muted">离线包不是整站备份；浏览器清理、设备丢失或口令遗忘可能造成未同步内容丢失。同步不会重放 AI、分享或社区操作。</p>{message?<p role="status">{message}</p>:null}{error?<p className="error" role="alert">{error}</p>:null}</section>
    {busy?<p role="status">正在处理本机操作，编辑、组织和页面操作已暂停。</p>:null}<fieldset className="offline-editing" disabled={busy} inert={busy}>
    <OfflineOrganizations value={value} repo={repo} onChange={refresh} onError={report}/>
    <OfflineConflicts items={value.queue.filter(q=>q.state==='conflict')} repo={repo} onChange={refresh} onError={report}/>
    <section className="panel"><h3>离线创建</h3><form className="offline-grid" onSubmit={e=>{e.preventDefault();void work(async()=>{const id=uuid();if(!title.trim())throw new Error('请输入标题');await edit(repo,'note.create',id,{title:title.trim(),kind,body_json:{},plain_text:'',notebook_id:notebook||null,tag_ids:[]});navigate(id);setTitle('')})}}><label>新笔记标题<input required maxLength={200} value={title} onChange={e=>setTitle(e.target.value)}/></label><label>类型<select value={kind} onChange={e=>setKind(e.target.value as typeof kind)}><option value="text">富文本笔记</option><option value="image">图片笔记</option></select></label><button className="secondary" disabled={busy||!value.syncEpoch}>创建并保存到本机</button></form><OfflinePdfPicker repo={repo} onChange={async id=>{await refresh();navigate(id)}} onError={report}/></section>
    <section className="panel"><h3>完整笔记列表</h3><div className="offline-grid"><label>本机搜索<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="标题或完整正文"/></label><label>文件夹<select value={folder} onChange={e=>setFolder(e.target.value)}><option value="">全部</option>{value.folders.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label><label>笔记本<select value={notebook} onChange={e=>setNotebook(e.target.value)}><option value="">全部</option>{value.notebooks.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label><label>标签<select value={tag} onChange={e=>setTag(e.target.value)}><option value="">全部</option>{value.tags.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label></div><label className="offline-check"><input type="checkbox" checked={trash} onChange={e=>setTrash(e.target.checked)}/>回收站</label><div className="note-list">{visible.map(n=><article className="note-card" key={n.id}><button className="note-open" onClick={()=>navigate(n.id)}><strong>{n.title}</strong><small>{n.kind==='text'?'富文本':'图片/PDF'} · revision（版本）{n.revision}</small></button><div className="note-actions"><button className="text-button" disabled={busy} onClick={()=>{if(dirty&&selected===n.id){report(new Error('请先保存界面修改'));return}void work(()=>edit(repo,n.deleted_at?'note.restore':'note.trash',n.id,{}))}}>{n.deleted_at?'明确恢复笔记':'移入回收站'}</button><button className="text-button" disabled={busy} onClick={()=>void work(async()=>{const id=await copyNote(repo,n.id);navigate(id)})}>保留真实副本</button></div></article>)}</div>{!visible.length?<p className="empty">没有符合条件的已缓存笔记。</p>:null}</section>
    {note?<OfflineNote key={note.id} note={note} value={value} repo={repo} onChange={refresh} onError={report} onDirty={setDirty}/>:null}
    <details className="panel"><summary>本机删除处理历史</summary>{value.history.filter(h=>String(h.value.reason||'').startsWith('server_deleted')).map((h,i)=><details key={`${h.at}:${i}`}><summary>{new Date(h.at).toLocaleString('zh-CN')} · {h.note_id.slice(0,8)}</summary><pre>{JSON.stringify(h.value,null,2)}</pre></details>)}</details>
    <details className="panel"><summary>持久同步队列（{count} 项未完成）</summary>{unfinished(value).map(q=><div key={q.operation.operation_id} className="offline-queue"><strong>{q.operation.kind}</strong><span>{q.state==='sending'?'已发送，重试沿用原编号':q.state==='conflict'?'需要核对':'等待同步'} · {q.operation.operation_id.slice(0,8)}</span>{q.error?<p className="error">{q.error}</p>:null}</div>)}</details>
    </fieldset>
  </main>
}
