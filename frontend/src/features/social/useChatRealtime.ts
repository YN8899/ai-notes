import {useEffect,useState} from 'react'
import {useQueryClient} from '@tanstack/react-query'
import {subscribeRealtime} from './sharedRealtime'

export function useChatRealtime(owner:string){
  const client=useQueryClient(),[status,setStatus]=useState('正在连接实时消息…')
  useEffect(()=>subscribeRealtime(owner,client,setStatus),[owner,client])
  return status
}
