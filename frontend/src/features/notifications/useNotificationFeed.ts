import {useEffect} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {api} from '../../api'
import {authSession} from '../../session'
import {advanceFeed,emptyFeed,refreshReadWindow,type FeedPage,type FeedState} from './feed'

export function useNotificationFeed(owner:string){
  const client=useQueryClient(),session=authSession.ticket(),key=['notices',owner,session.id]
  const query=useQuery({queryKey:key,enabled:!!owner,refetchInterval:30000,queryFn:async()=>{
    let state=client.getQueryData<FeedState>(key)||emptyFeed(),previousRevision=state.revision
    // Ten pages per query keeps a growing backlog from monopolizing the UI.
    // More backlog resumes from the acknowledged cursor after this query.
    for(let count=0;count<10;count++){
      let page=await api<FeedPage>(`/notifications/feed?after_seq=${state.after}&limit=100`)
      if(page.head_seq<state.after){state=emptyFeed();page=await api<FeedPage>('/notifications/feed?after_seq=0&limit=100')}
      state=advanceFeed(state,page)
      if(!page.has_more)break
    }
    // Read changes have an independent revision and never alter create seq.
    // Refresh the SAME seq window. Legacy list ordering uses created_at/id and
    // can omit visible rows when a batch has identical creation timestamps.
    if(state.revision!==previousRevision&&state.items.length){
      const lower=Math.min(...state.items.map(row=>row.seq))-1
      const current=await api<FeedPage>(`/notifications/feed?after_seq=${lower}&limit=100`)
      state=refreshReadWindow(state,current)
    }
    return state
  }})
  useEffect(()=>{if(!query.data?.pending)return;const timer=setTimeout(()=>void query.refetch(),100);return()=>clearTimeout(timer)},[query.data,query.refetch])
  return {...query,catchingUp:query.data?.pending,data:query.data?.items.slice(0,30)}
}
