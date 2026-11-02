import {lazy,Suspense,useEffect,useRef,useState} from 'react'
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query'
import {api,date} from '../../api'
import {createUuid} from '../../uuid'
import {NoteError} from '../notes/NoteError'
import type {Note,NoteList} from '../notes/types'
import {queryString} from '../social/social'
import {clearEditingDraft,readEditingDraft,writeEditingDraft} from './draftCache'
import {contentOf,editorBody,emptyContent,parseTags,postTypes,statuses,type Content,type Draft,type PostType} from './types'

const RichEditor=lazy(()=>import('../notes/RichEditor').then(module=>({default:module.RichEditor})))

export default function PostEditor({owner,postId,onClose,onCreated,onRead}:{owner:string;postId:string;onClose:()=>void;onCreated:(id:string)=>void;onRead:(id:string)=>void}){
  const query=useQuery({queryKey:['community',owner,'draft',postId],queryFn:()=>api<Draft>('/community/posts/'+postId+'/draft'),enabled:postId!=='new',refetchInterval:3000})
  if(postId!=='new'&&!query.data)return <section className="panel"><button className="text-button" onClick={onClose}>返回我的帖子</button><NoteError error={query.error}/>{query.isPending?<p role="status">正在读取草稿…</p>:<button className="secondary" onClick={()=>void query.refetch()}>重新读取草稿</button>}</section>
  return <DraftEditor key={owner+':'+postId} owner={owner} postId={postId} remote={query.data||null} refresh={async()=>{const result=await query.refetch();return result.isSuccess?result.data:undefined}} remoteError={query.error} onClose={onClose} onCreated={onCreated} onRead={onRead}/>
}

function DraftEditor({owner,postId,remote,refresh,remoteError,onClose,onCreated,onRead}:{owner:string;postId:string;remote:Draft|null;refresh:()=>Promise<Draft|undefined>;remoteError:unknown;onClose:()=>void;onCreated:(id:string)=>void;onRead:(id:string)=>void}){
  const client=useQueryClient()
  const [editing,setEditing]=useState(()=>readEditingDraft(owner,postId)||{base:remote,content:remote?contentOf(remote):emptyContent(),tags:remote?.tags.join('，')||''})
  const {base,content,tags}=editing
  const [epoch,setEpoch]=useState(0),[notice,setNotice]=useState(''),[noteCursor,setNoteCursor]=useState(''),[sourceChoice,setSourceChoice]=useState(content.source_note_id||'')
  const [backup,setBackup]=useState<Content|null>(null)
  const [reloading,setReloading]=useState(false)
  const publishReceipt=useRef<{revision:number;request_key:string}|null>(null)
  const dirty=JSON.stringify(content)!==JSON.stringify(base?contentOf(base):emptyContent())||tags!==(base?.tags.join('，')||'')
  const preparing=remote?.publish_status==='preparing'
  const conflict=!!(remote&&base&&remote.revision!==base.revision&&dirty)
  useEffect(()=>{writeEditingDraft(owner,postId,editing)},[owner,postId,editing])
  useEffect(()=>{
    if(remote&&remote.revision!==base?.revision&&!dirty){setEditing({base:remote,content:contentOf(remote),tags:remote.tags.join('，')});setEpoch(v=>v+1)}
  },[remote,base?.revision,dirty])
  useEffect(()=>{const warn=(event:BeforeUnloadEvent)=>{if(dirty){event.preventDefault();event.returnValue=''}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn)},[dirty])
  function patch(changes:Partial<Content>){setEditing(old=>({...old,content:{...old.content,...changes}}))}
  function accept(data:Draft){setEditing({base:data,content:contentOf(data),tags:data.tags.join('，')});setEpoch(v=>v+1);publishReceipt.current=null;client.setQueryData(['community',owner,'draft',data.id],data)}
  const notes=useQuery({queryKey:['community',owner,'source-notes',noteCursor],queryFn:()=>api<NoteList>('/notes'+queryString({limit:20,cursor:noteCursor}))})
  const source=useMutation({mutationFn:(id:string)=>api<Note>('/notes/'+id),onSuccess:note=>{patch({title:content.title||note.title,body_json:note.body_json,plain_text:note.plain_text,source_note_id:note.id,source_revision:note.revision});setEpoch(v=>v+1);setNotice('已读取自己的源笔记。发布时会复制图片，原笔记后续变更不影响已发布副本。')}})
  const save=useMutation({mutationFn:()=>{if(!content.title.trim())throw new Error('请填写帖子标题');const payload={...content,title:content.title.trim(),tags:parseTags(tags)};return postId==='new'?api<Draft>('/community/posts','POST',payload):api<Draft>('/community/posts/'+postId,'PATCH',{...payload,revision:base?.revision})},onSuccess:data=>{accept(data);setNotice('草稿已保存，公开内容需要显式发布后才更新。');void client.invalidateQueries({queryKey:['community',owner,'mine']});if(postId==='new'){clearEditingDraft(owner,'new');onCreated(data.id)}}})
  const publish=useMutation({mutationFn:()=>{if(!base||dirty)throw new Error('请先保存当前草稿');if(!publishReceipt.current||publishReceipt.current.revision!==base.revision)publishReceipt.current={revision:base.revision,request_key:createUuid()};return api('/community/posts/'+postId+'/publish','POST',publishReceipt.current)},onSuccess:async()=>{setNotice('发布请求已保存，正在准备独立社区副本；全部准备完成后才公开。');await refresh();void client.invalidateQueries({queryKey:['community',owner]})}})
  const retry=useMutation({mutationFn:()=>api('/community/posts/'+postId+'/retry','POST',{revision:remote?.revision}),onSuccess:async()=>{setNotice('已重新排队准备副本。');await refresh();void client.invalidateQueries({queryKey:['community',owner]})}})
  const withdraw=useMutation({mutationFn:()=>api<Draft>('/community/posts/'+postId+'/withdraw','POST',{revision:remote?.revision}),onSuccess:data=>{if(!dirty)accept(data);setNotice('已撤回；公开正文与附件的新读取立即停止。');void refresh();void client.invalidateQueries({queryKey:['community',owner]})}})
  const busy=reloading||save.isPending||publish.isPending||retry.isPending||withdraw.isPending||source.isPending
  const state=remote||base
  async function reload(){if(busy)return;if(dirty&&!window.confirm('重新读取会替换尚未保存的编辑。确定继续？'))return;if(postId!=='new'){setReloading(true);try{const latest=await refresh();if(latest){accept(latest);setNotice('已重新读取服务器草稿。')}}finally{setReloading(false)}}else{setEditing({base:null,content:emptyContent(),tags:''});setEpoch(v=>v+1)}}
  return <section className="panel community-editor">
    <div className="community-actions"><button className="text-button" disabled={busy} onClick={onClose}>返回我的帖子</button>{state?.status==='published'?<button className="secondary" disabled={busy} onClick={()=>onRead(state.id)}>查看当前公开版本</button>:null}</div>
    <h2>{postId==='new'?'写一篇新帖子':'编辑我的帖子'}</h2>
    <p className="muted">保存仅更新草稿。点击发布后，社区展示独立副本；原笔记、AI 会话和私人学习数据不会被授权。</p>
    {state?<p role="status">{statuses[state.status]} · 草稿版本 {base?.revision}{dirty?' · 有未保存修改':''} · {date(state.updated_at)}</p>:<p role="status">新草稿，尚未保存</p>}
    {state?.publish_status==='preparing'?<p role="status" className="community-notice">正在复制和校验图片，完成后自动公开。已有公开版本在更新完成前保留；可以撤回取消发布。</p>:state?.publish_status==='failed'?<p role="alert" className="error">副本准备失败，内容未更新到社区。{state.publish_error?'错误标识：'+state.publish_error:''} 可重试准备。</p>:null}
    {state?.moderation_reason?<p role="alert" className="error">下架原因：{state.moderation_reason}。请联系管理员处理；保存草稿不会绕过下架。</p>:null}
    <NoteError error={remoteError||save.error||source.error||publish.error||retry.error||withdraw.error}/>
    {conflict?<p className="error" role="alert">服务器已有新版本，当前输入已保留。请先复制需要的内容，再重新读取服务器草稿，避免覆盖其他编辑。</p>:null}
    <p role="status">{notice}</p>
    <fieldset className="community-fields" disabled={busy||preparing}>
      <label>帖子标题<input required maxLength={200} value={content.title} onChange={event=>patch({title:event.target.value})}/></label>
      <div className="community-field-row"><label>学科<input maxLength={80} value={content.subject} onChange={event=>patch({subject:event.target.value})} placeholder="例如：高等数学"/></label><label>帖子类型<select value={content.post_type} onChange={event=>patch({post_type:event.target.value as PostType})}>{Object.entries(postTypes).map(([value,label])=><option value={value} key={value}>{label}</option>)}</select></label></div>
      <label>自由标签<input value={tags} onChange={event=>setEditing(old=>({...old,tags:event.target.value}))} placeholder="用逗号分隔，最多12个，每个最多32字"/></label>
      <details className="community-source"><summary>从自己的笔记准备副本（可选）</summary><p className="muted">读取会替换此草稿正文，原笔记保持不变。图片在发布时复制；源笔记变更后，请重新读取来源版本再保存。</p><label>我的笔记<select value={sourceChoice} onChange={event=>setSourceChoice(event.target.value)}><option value="">选择自己的笔记</option>{content.source_note_id&&!notes.data?.items.some(row=>row.id===content.source_note_id)?<option value={content.source_note_id}>当前已关联的笔记</option>:null}{notes.data?.items.map(note=><option value={note.id} key={note.id}>{note.title}（{note.kind==='image'?'图片':'文字'}）</option>)}</select></label><NoteError error={notes.error}/><div className="community-actions"><button className="text-button" disabled={!noteCursor||notes.isFetching} onClick={()=>setNoteCursor('')}>笔记首页</button><button className="text-button" disabled={!notes.data?.next_cursor||notes.isFetching} onClick={()=>setNoteCursor(notes.data?.next_cursor||'')}>下一页笔记</button><button className="secondary" disabled={!sourceChoice} onClick={()=>{if((content.plain_text||Object.keys(content.body_json).length)&&!window.confirm('这会用源笔记当前正文替换草稿正文。确定读取？'))return;source.mutate(sourceChoice)}}>读取所选笔记副本</button>{content.source_note_id?<button className="text-button" onClick={()=>{patch({source_note_id:null,source_revision:null});setSourceChoice('')}}>解除来源，改为原创</button>:null}</div></details>
      {content.source_note_id?<p className="muted">已关联自己的笔记，来源版本 {content.source_revision}。公开阅读器不会链接私人原笔记。</p>:null}
    </fieldset>
    <div inert={busy||preparing?true:undefined}><Suspense fallback={<p>正在打开正文编辑器…</p>}><RichEditor key={epoch} initial={editorBody(content.body_json,content.plain_text)} onChange={(body,plain)=>patch({body_json:body,plain_text:plain})} onBeforeImport={()=>{setBackup(content);setNotice('导入前正文保留在当前编辑器，可点击恢复导入前正文。')}}/></Suspense></div>
    {backup?<button className="secondary" disabled={busy||preparing} onClick={()=>{setEditing(old=>({...old,content:backup}));setEpoch(v=>v+1);setBackup(null)}}>恢复导入前正文</button>:null}
    <div className="community-actions"><button className="primary" disabled={busy||preparing||(!dirty&&!!base)} onClick={()=>save.mutate()}>{save.isPending?'正在保存…':base?'保存草稿':'创建草稿'}</button><button className="secondary" disabled={busy||preparing} onClick={reload}>重新读取服务器草稿</button>{base?<><button className="primary" disabled={busy||preparing||dirty||state?.status==='hidden'} onClick={()=>publish.mutate()}>{publish.isPending?'正在提交…':state?.status==='published'?'发布已保存的更新':'发布已保存的草稿'}</button>{state?.publish_status==='failed'?<button className="secondary" disabled={busy||dirty||state.status==='hidden'} onClick={()=>retry.mutate()}>重试上次发布</button>:null}{state?.status!=='hidden'&&(state?.status==='published'||preparing)?<button className="secondary" disabled={busy} onClick={()=>withdraw.mutate()}>撤回公开内容并取消发布</button>:null}</>:null}</div>
    <p className="muted">未保存的输入会在当前标签页、当前登录期间保留。退出登录会清除这份临时输入；请及时保存草稿。</p>
  </section>
}
