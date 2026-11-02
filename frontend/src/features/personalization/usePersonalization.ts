import {useSyncExternalStore} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {api} from '../../api'
import {authSession} from '../../session'
import type {PreferencesOut} from './types'
export function usePersonalization(owner:string,sessionId?:string){
  const session=useSyncExternalStore(authSession.subscribe,authSession.snapshot)
  const id=sessionId??session.id
  const key=['personalization',owner,id,'preferences'] as const
  const query=useQuery({queryKey:key,queryFn:()=>api<PreferencesOut>('/personalization/preferences'),enabled:!!owner&&session.phase==='active'&&session.owner===owner&&session.id===id,retry:false})
  const client=useQueryClient()
  return {...query,publish:(data:PreferencesOut)=>{if(authSession.snapshot().phase==='active'&&authSession.snapshot().owner===owner&&authSession.snapshot().id===id)client.setQueryData(key,data)}}
}
