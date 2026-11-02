import {lazy,Suspense} from 'react'
import {useQuery} from '@tanstack/react-query'
import {api} from '../../api'
import {NoteError} from '../notes/NoteError'
import {ProtectedImage} from './ProtectedAsset'
import type {SharedNote} from './social'
const ReadOnlyBody=lazy(()=>import('./ReadOnlyBody'))
export default function SharedReader({owner,shareId,onClose}:{owner:string;shareId:string;onClose:()=>void}){
  const query=useQuery({queryKey:['social',owner,'share',shareId],queryFn:()=>api<SharedNote>('/social/shares/'+shareId),refetchInterval:20000,retry:false})
  return <section className="panel shared-reader"><button className="text-button" onClick={onClose}>返回分享列表</button><NoteError error={query.error}/>{query.isPending?<p role="status">正在核对分享权限…</p>:query.isError?<button className="secondary" onClick={()=>void query.refetch()}>重新核对分享权限</button>:query.data?<><h2>{query.data.grant.title}</h2><p className="muted">{query.data.grant.owner.display_name} 的只读分享 · 当前版本 {query.data.grant.source_revision}</p><p className="muted">此页面仅提供阅读。分享者可随时撤销授权。</p>{query.data.grant.kind==='text'?<Suspense fallback={<p>正在打开正文…</p>}><ReadOnlyBody body={query.data.body_json} plainText={query.data.plain_text}/></Suspense>:query.data.pages.map(page=><ProtectedImage key={page.id+':'+page.revision} path={page.image_url.replace(/^\/api\/v1/,'')} alt={`第${page.position+1}页 · ${page.filename}`}/> )}</>:null}</section>
}
