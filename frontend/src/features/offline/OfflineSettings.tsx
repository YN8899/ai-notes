import {useState,useSyncExternalStore} from 'react'
import {api} from '../../api.ts'
import {createVault,unlockVault,vaultSnapshot,subscribeVault} from './vault.ts'
import {OfflineRepository} from './repository.ts'
import {cacheSnapshot} from './cache.ts'
import type {Note} from './types.ts'
import './offline.css'
export default function OfflineSettings({owner,onOpen}:{owner:string;onOpen:()=>void}){
  const vault=useSyncExternalStore(subscribeVault,vaultSnapshot),[password,setPassword]=useState(''),[repeat,setRepeat]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[scope,setScope]=useState<'all'|'selected'>('all'),[budget,setBudget]=useState(150),[notes,setNotes]=useState<Note[]>([]),[selected,setSelected]=useState<string[]>([]),[more,setMore]=useState(true),[deleted,setDeleted]=useState(false)
  const cap=vault.cap?.owner===owner?vault.cap:null
  const work=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn()}catch(e){setError(e instanceof Error?e.message:'操作失败')}finally{setBusy(false)}}
  async function create(){
    if(password!==repeat)throw new Error('两次本机离线口令不一致')
    const next=await createVault(owner,password,async()=>{const me=await api<{id:string}>('/auth/me');if(me.id!==owner)throw new Error('请核对当前服务器账号')})
    setPassword('');setRepeat('')
    // Establish the immutable synchronization epoch before accepting any local operation.
    await cacheSnapshot(new OfflineRepository(next),'selected',[],budget*1024*1024,setMessage)
    setMessage('本机离线库已创建。请选择资料范围并完成缓存，再断网使用。')
  }
  async function loadNotes(reset=false){
    const offset=reset?0:notes.length,result=await api<{items:Note[];total?:number}>(`/notes?deleted=${deleted}&limit=100&offset=${offset}`)
    setNotes(old=>reset?result.items:[...old,...result.items]);setMore(result.items.length===100)
  }
  return <section className="panel offline-settings"><h3>本机离线资料</h3><p className="muted">仅保存本人笔记与组织。分享、社区和 AI 新调用仍需联网。离线口令只解锁此设备密文，<strong>不是服务器登录密码</strong>，遗忘后无法恢复未同步内容。</p>
    {!cap?<><label>独立的本机离线口令<input type="password" autoComplete="new-password" minLength={10} maxLength={256} value={password} onChange={e=>setPassword(e.target.value)}/></label><label>再次输入（首次创建时填写）<input type="password" autoComplete="new-password" maxLength={256} value={repeat} onChange={e=>setRepeat(e.target.value)}/></label><div className="note-actions"><button className="primary" disabled={busy||password.length<10} onClick={()=>void work(create)}>首次创建离线库</button><button className="secondary" disabled={busy||password.length<10} onClick={()=>void work(async()=>{await unlockVault(owner,password);setPassword('');setRepeat('');setMessage('已解锁本机离线库')})}>解锁已有离线库</button></div></>:<p role="status">当前账号的本机离线库已解锁。</p>}
    <div className="offline-grid"><label>缓存范围<select value={scope} onChange={e=>setScope(e.target.value as 'all'|'selected')}><option value="all">全部本人笔记（含回收站）</option><option value="selected">选择本人笔记</option></select></label><label>完整缓存预算（MiB）<input type="number" min={20} max={500} value={budget} onChange={e=>setBudget(Number(e.target.value))}/></label></div>
    {scope==='selected'?<div><label className="offline-check"><input type="checkbox" checked={deleted} onChange={e=>{setDeleted(e.target.checked);setNotes([]);setMore(true)}}/>列出回收站笔记</label><button className="secondary" disabled={busy} onClick={()=>void work(()=>loadNotes(true))}>读取可选笔记</button><fieldset className="offline-choices"><legend>已选择 {selected.length} 本</legend>{notes.map(n=><label className="offline-check" key={n.id}><input type="checkbox" checked={selected.includes(n.id)} onChange={e=>setSelected(old=>e.target.checked?[...old,n.id]:old.filter(id=>id!==n.id))}/>{n.title}</label>)}</fieldset>{more&&notes.length?<button className="secondary" disabled={busy} onClick={()=>void work(()=>loadNotes())}>加载更多本人笔记</button>:null}</div>:null}
    <div className="note-actions"><button className="primary" disabled={!cap||busy} onClick={()=>void work(async()=>{await cacheSnapshot(new OfflineRepository(cap! ),scope,selected,budget*1024*1024,setMessage)})}>校验并加密缓存资料</button><button className="secondary" disabled={busy} onClick={onOpen}>打开本机离线空间</button><button className="secondary" disabled={busy||!navigator.storage?.persist} onClick={()=>void work(async()=>{setMessage(await navigator.storage.persist()?'浏览器已允许持久保存；设备丢失仍无恢复保证':'浏览器未允许持久保存，可能按自身策略清理资料')})}>申请浏览器持久保存</button></div>
    <p className="muted">新快照全部校验通过后一次切换；容量超限、缺失文件或已有未同步修改时明确停止，不替换旧缓存。刷新失败产生的加密暂存文件会占用空间，可在核对未同步内容后明确清除整库。</p>{message?<p role="status">{message}</p>:null}{error?<p className="error" role="alert">{error}</p>:null}{vault.warning?<p className="error" role="alert">{vault.warning}</p>:null}
  </section>
}
