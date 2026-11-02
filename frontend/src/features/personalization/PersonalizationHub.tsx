import {lazy,Suspense,useState,useSyncExternalStore} from 'react'
import {authSession} from '../../session'
import {NoteError} from '../notes/NoteError'
import {usePersonalization} from './usePersonalization'
import './personalization.css'
const PreferencesPanel=lazy(()=>import('./PreferencesPanel').then(m=>({default:m.PreferencesPanel})))
const RecommendationsPanel=lazy(()=>import('./RecommendationsPanel').then(m=>({default:m.RecommendationsPanel})))
const AnalysisPanel=lazy(()=>import('./AnalysisPanel').then(m=>({default:m.AnalysisPanel})))
export default function PersonalizationHub({owner,onRead,onLatest}:{owner:string;onRead:(id:string)=>void;onLatest:()=>void}){
  const session=useSyncExternalStore(authSession.subscribe,authSession.snapshot)
  return session.phase==='active'&&session.owner===owner?<Hub key={owner+':'+session.id} owner={owner} sessionId={session.id} onRead={onRead} onLatest={onLatest}/>:null
}
function Hub({owner,sessionId,onRead,onLatest}:{owner:string;sessionId:string;onRead:(id:string)=>void;onLatest:()=>void}){
  const [tab,setTab]=useState<'preferences'|'recommendations'|'analysis'|null>(null)
  const preferences=usePersonalization(owner,sessionId)
  return <div className="personalization-stack"><nav className="social-actions personalization-navigation" aria-label="本人个性化功能"><button className={tab==='preferences'?'primary':'secondary'} onClick={()=>setTab('preferences')}>首页与个性化偏好</button><button className={tab==='recommendations'?'primary':'secondary'} onClick={()=>setTab('recommendations')}>个性化社区推荐</button><button className={tab==='analysis'?'primary':'secondary'} onClick={()=>setTab('analysis')}>本人学习事实与 AI 分析</button>{tab?<button className="text-button" onClick={()=>setTab(null)}>收起</button>:null}</nav><NoteError error={preferences.error}/>{preferences.error?<button className="secondary" onClick={()=>void preferences.refetch()}>重新读取本人偏好</button>:null}{tab&&preferences.isPending?<p role="status">正在读取本人偏好…</p>:null}{preferences.data&&tab?<Suspense fallback={<p role="status">正在打开个性化功能…</p>}>{tab==='preferences'?<PreferencesPanel initial={preferences.data} onSaved={preferences.publish} onBack={()=>setTab(null)}/>:tab==='recommendations'?<RecommendationsPanel key={preferences.data.revision} owner={owner} sessionId={sessionId} preferences={preferences.data} onRead={onRead} onLatest={onLatest} onPreferences={()=>setTab('preferences')}/>:<AnalysisPanel owner={owner} sessionId={sessionId} preferences={preferences.data} onPreferences={()=>setTab('preferences')}/>}</Suspense>:null}</div>
}
