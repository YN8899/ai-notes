import {saveNoteDraft} from './queue.ts'
import {useEffect,useRef,useState} from 'react'
import {RichEditor} from '../notes/RichEditor.tsx'
import {OfflineRepository} from './repository.ts'
import type {Note,Workspace} from './types.ts'
import {equal} from './merge.ts'
import {LocalDraftConflict,noteDraft,type NoteDraft} from './draft.ts'
import OfflineAssets from './OfflineAssets.tsx'
export default function OfflineNote({note,value,repo,onChange,onError,onDirty}:{note:Note;value:Workspace;repo:OfflineRepository;onChange:()=>Promise<void>;onError:(error:unknown)=>void;onDirty:(dirty:boolean)=>void}){
  const baseline=useRef(structuredClone(note)),[draft,setDraft]=useState(()=>noteDraft(note)),[status,setStatus]=useState('已从加密资料读取'),[busy,setBusy]=useState(false),[editorVersion,setEditorVersion]=useState(0),[conflict,setConflict]=useState<LocalDraftConflict|null>(null),generation=useRef(0)
  // A refresh may advance the saved note, but never replace a dirty editor automatically.
  useEffect(()=>{if(!equal(note,baseline.current)&&equal(draft,noteDraft(baseline.current))){baseline.current=structuredClone(note);setDraft(noteDraft(note));setEditorVersion(version=>version+1)}},[note])
  function change(patch:Partial<NoteDraft>){generation.current++;setDraft(old=>({...old,...patch}));setStatus('有尚未保存到本机的修改');onDirty(true)}
  async function save(){
    const ticket=generation.current;setBusy(true);setStatus('正在加密并原子写入本机资料和队列…')
    try{
      if(!draft.title.trim()||draft.title.length>200)throw new Error('标题须为 1–200 个字符')
      const saved=await saveNoteDraft(repo,note.id,baseline.current,draft)
      if(ticket===generation.current){baseline.current=structuredClone(saved);setDraft(noteDraft(saved));setEditorVersion(version=>version+1);setConflict(null);setStatus('已加密保存到本机，等待联网同步');onDirty(false)}else setStatus('先前内容已保存，仍有新修改未保存')
      await onChange()
    }catch(error){if(error instanceof LocalDraftConflict)setConflict(error);setStatus('保存失败，界面修改尚未保存；请保留页面并核对');onError(error)}finally{setBusy(false)}
  }
  async function reload(){
    if(!window.confirm('明确重新载入本机已保存版本？这会替换当前界面草稿，请先从下方复制 JSON 保存。'))return
    setBusy(true);try{const current=(await repo.value()).notes.find(n=>n.id===note.id);if(!current)throw new Error('本机笔记已移除，请先复制界面草稿');baseline.current=structuredClone(current);setDraft(noteDraft(current));setEditorVersion(version=>version+1);generation.current++;setConflict(null);onDirty(false);setStatus('已明确重新载入本机保存版本');await onChange()}catch(error){onError(error)}finally{setBusy(false)}
  }
  return <section className="panel note-detail"><fieldset disabled={busy} inert={busy} className="offline-editing"><label>笔记标题<input maxLength={200} disabled={!!note.deleted_at} value={draft.title} onChange={e=>change({title:e.target.value})}/></label><div className="note-metadata"><label>所属笔记本<select value={draft.notebook_id||''} disabled={!!note.deleted_at} onChange={e=>change({notebook_id:e.target.value||null})}><option value="">未分类</option>{value.notebooks.map(n=><option key={n.id} value={n.id}>{n.name}</option>)}</select></label><fieldset><legend>标签</legend>{value.tags.map(tag=><label className="offline-check" key={tag.id}><input type="checkbox" disabled={!!note.deleted_at} checked={draft.tag_ids.includes(tag.id)} onChange={e=>change({tag_ids:e.target.checked?[...draft.tag_ids,tag.id]:draft.tag_ids.filter(id=>id!==tag.id)})}/>{tag.name}</label>)}</fieldset></div>
    {note.deleted_at?<p role="status">本笔记在回收站，恢复后可编辑。同步不会因为编辑自动恢复服务器已删除的笔记。</p>:null}
    {note.kind==='text'?<RichEditor key={editorVersion} initial={draft.body_json} readOnly={busy||!!note.deleted_at} onChange={(body_json,plain_text)=>change({body_json,plain_text})} onBeforeImport={async()=>{await repo.mutate(w=>{w.history.push({note_id:note.id,value:{body_json:draft.body_json,plain_text:draft.plain_text},at:new Date().toISOString()})})}}/>:<OfflineAssets repo={repo} value={value} noteId={note.id} onChange={onChange} onError={onError}/>}
    <div className="note-actions"><button className="primary" disabled={busy||!!note.deleted_at} onClick={()=>void save()}>保存到本机加密队列</button><span role="status" className="save-status">{status}</span></div></fieldset>
    {conflict?<div role="alert"><p>{conflict.message}这是本机视图之间的冲突，尚未发送服务器。</p><label>当前界面草稿 JSON（可复制）<textarea readOnly rows={8} value={JSON.stringify(draft,null,2)}/></label><label>检测到的本机保存版本 JSON<textarea readOnly rows={8} value={JSON.stringify(conflict.current,null,2)}/></label><button className="secondary" disabled={busy} onClick={()=>void reload()}>复制草稿后，明确重载本机版本</button></div>:null}
    <details><summary>本机保留的编辑历史（{value.history.filter(h=>h.note_id===note.id).length}）</summary>{value.history.filter(h=>h.note_id===note.id).slice().reverse().map((h,i)=><details key={`${h.at}:${i}`}><summary>{new Date(h.at).toLocaleString('zh-CN')}</summary><pre>{JSON.stringify(h.value,null,2)}</pre></details>)}</details>
  </section>
}
