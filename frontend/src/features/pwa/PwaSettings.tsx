import {useEffect,useState} from 'react'
import {useMutation,useQuery} from '@tanstack/react-query'
import {api} from '../../api'
import {readSession} from '../../session.ts'
import {disableBrowserPush,enableBrowserPush,pushSupported,readBinding,sendBrowserPushTest} from './push'
import {activateUpdate,installApp,pwaSnapshot,subscribePwa} from './register'
import {canTestPush,type PushConfig,type PushStatus} from './pushCore.ts'

export default function PwaSettings({owner}:{owner:string}){
  const [,render]=useState(0),[message,setMessage]=useState('')
  useEffect(()=>subscribePwa(()=>render(value=>value+1)),[])
  const state=pwaSnapshot(),binding=readBinding(),supported=pushSupported()
  const config=useQuery({queryKey:['push-config',owner],queryFn:()=>api<PushConfig>('/push/config')})
  const status=useQuery({queryKey:['push-status',owner],queryFn:()=>api<PushStatus[]>('/push/subscriptions')})
  const ours=binding?.owner===owner&&binding.scope===readSession()?.scope&&status.data?.some(row=>row.id===binding.id)
  const enabled=ours&&status.data?.some(row=>row.id===binding?.id&&row.enabled)
  const testReady=supported&&canTestPush(binding,owner,readSession()?.scope||'',config.data,status.data)
  const test=useMutation({mutationFn:()=>sendBrowserPushTest(owner,config.data!),onSuccess:()=>setMessage('测试提醒已排队；不保证设备送达，请以站内提醒记录为准。重复点击或重试不会重复创建通知。')})
  const mutation=useMutation({mutationFn:async(operation:Promise<unknown>)=>{
    await operation
    setMessage('推送设置已更新。站内提醒记录为准。')
  },onSuccess:()=>status.refetch()})
  return <section className="panel"><h2>安装与浏览器提醒</h2><p>安装到桌面后可断网重新打开应用。个人资料离线能力以笔记空间提示为准。</p>
    {state.installed?<p>已在独立应用窗口中打开。</p>:<><button className="secondary" onClick={()=>void installApp()} disabled={!state.installAvailable}>安装知页</button><p className="muted">Chrome / Edge：浏览器菜单 → 安装应用。iPhone / iPad：Safari 分享 → 添加到主屏幕，再从主屏幕打开。</p></>}
    {state.waiting&&<p role="status">发现新版本。请先保存正在编辑的内容。<button className="secondary" onClick={activateUpdate}>准备应用更新</button></p>}
    {state.activated&&<p role="status">更新已就绪。<button className="secondary" onClick={()=>window.location.reload()}>保存完成，刷新加载</button></p>}
    {state.error&&<p role="status">{state.error}</p>}
    <p>{enabled?'此会话已开启浏览器推送。':'此会话的浏览器推送未开启。'}只显示“知页有新的站内提醒”，点击打开首页并检查登录状态。</p>
    {!supported&&<p>此浏览器或运行方式暂不支持推送。手机可先安装后重试；站内提醒仍可使用。</p>}
    {config.data&&!config.data.enabled&&<p>服务尚未配置推送；站内提醒仍可使用。</p>}
    <div style={{display:'flex',flexWrap:'wrap',gap:8,marginBottom:12}}><button className="primary" disabled={mutation.isPending||!supported||!config.data?.enabled||Boolean(enabled)} onClick={()=>{if(config.data)mutation.mutate(enableBrowserPush(owner,config.data))}}>开启 / 重新关联本设备</button>
    <button className="secondary" disabled={mutation.isPending||!ours} onClick={()=>mutation.mutate(disableBrowserPush(owner))}>关闭此会话推送</button>
    <button className="secondary" disabled={mutation.isPending||test.isPending||!testReady} onClick={()=>test.mutate()}>发送测试提醒</button></div>
    <p className="muted">开启时将此设备关联到当前登录，同一账号旧会话的设备关联随即失效。其他账号须先退出或在原会话关闭推送；权限拒绝可到浏览器设置修改。网络中断时仍请完成退出；服务端会话撤销后不会新增发送。</p>
    {(mutation.error||test.error||config.error||status.error)&&<p role="alert">{(mutation.error||test.error||config.error||status.error)?.message}</p>}{message&&<p role="status">{message}</p>}
  </section>
}
