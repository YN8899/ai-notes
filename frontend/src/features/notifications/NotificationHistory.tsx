import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {api,date} from '../../api'
import {authSession} from '../../session'
import type {FeedPage} from './feed'

export function NotificationHistory({owner,onRead,readPending}:{owner:string;onRead:(id:string)=>void;readPending:boolean}){
  const [open,setOpen]=useState(false),[after,setAfter]=useState(0),session=authSession.ticket()
  const history=useQuery({queryKey:['notices',owner,'history',session.id,after],enabled:open,refetchInterval:open?30000:false,
    queryFn:()=>api<FeedPage>(`/notifications/feed?after_seq=${after}&limit=100`)})
  return <details onToggle={event=>setOpen(event.currentTarget.open)}><summary>查看通知历史（每页100条，按创建顺序）</summary>
    {history.isPending&&open?<p role="status">正在读取通知历史…</p>:null}
    {history.isError?<p role="alert">通知历史暂不可用。<button className="text-button" onClick={()=>void history.refetch()}>重新读取</button></p>:null}
    {history.data?.items.map(notice=><div className="notice" key={notice.id}><span className={'notice-dot '+(notice.read_at?'read':'')}/><div><strong>{notice.message}</strong><small>{date(notice.created_at)}</small></div>{!notice.read_at?<button className="text-button" disabled={readPending} onClick={()=>onRead(notice.id)}>已读</button>:null}</div>)}
    <button className="text-button" disabled={!after} onClick={()=>setAfter(0)}>历史首页</button>
    <button className="text-button" disabled={!history.data?.has_more||history.isFetching} onClick={()=>setAfter(history.data?.next_after_seq||after)}>继续读取历史通知</button>
  </details>
}
