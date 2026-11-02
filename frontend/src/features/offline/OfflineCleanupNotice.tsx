import {useState,useSyncExternalStore} from 'react'
import {clearVault,subscribeVault,vaultSnapshot} from './vault.ts'
export default function OfflineCleanupNotice(){
  const state=useSyncExternalStore(subscribeVault,vaultSnapshot),[busy,setBusy]=useState(false)
  if(!state.warning||!state.failedClearOwner)return null
  return <aside role="alert" className="error offline-cleanup-notice"><p>{state.warning}</p><button className="secondary" disabled={busy} onClick={()=>{if(!window.confirm('重试清除提示中账号的本机离线资料？其全部未同步内容也会永久删除，服务器和其他账号不受影响。'))return;setBusy(true);void clearVault(state.failedClearOwner!).catch(()=>{}).finally(()=>setBusy(false))}}>重试清除该账号离线资料</button></aside>
}
