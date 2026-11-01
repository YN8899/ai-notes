import {useState,useSyncExternalStore} from 'react'
import {authSession} from '../../session'
import {createUuid} from '../../uuid'
import {useReviewMutation} from './ReviewMutation'
import {matchingReviewPlan,type ReviewPlan} from './reviews'
import './reviews.css'
/** Existing owned objects are selected by their normal workspace, never by pasted UUID. */
export function ReviewJoin({owner,kind,id,onGoReview}:{owner:string;kind:'note'|'quiz';id:string;onGoReview?:(planId?:string)=>void}){const session=useSyncExternalStore(authSession.subscribe,authSession.snapshot);if(session.phase!=='active'||session.owner!==owner)return null;return <Join key={owner+':'+session.id+':'+kind+':'+id} owner={owner} sessionId={session.id} kind={kind} id={id} onGoReview={onGoReview}/>}
function Join({owner,sessionId,kind,id,onGoReview}:{owner:string;sessionId:string;kind:'note'|'quiz';id:string;onGoReview?:(planId?:string)=>void}){
  const [plan,setPlan]=useState<ReviewPlan|null>(null),mutation=useReviewMutation(owner,sessionId,receipt=>setPlan(matchingReviewPlan(receipt.plan,kind,id)))
  return <section className="panel review-join" aria-label="加入复习"><div className="card-actions"><button type="button" className="secondary" disabled={mutation.blocked||!!plan} onClick={()=>void mutation.send({path:'/learning/review-plans',method:'POST',body:{kind,[kind+'_id']:id,strategy:'manual',request_key:createUuid()}})}>{plan?'已加入独立复习计划':'加入复习'}</button>{onGoReview?<button type="button" className="text-button" onClick={()=>onGoReview(plan?.id)}>打开复习中心</button>:null}<small className="muted">为当前{kind==='note'?'笔记':'测验'}建立独立的手动计划，可在复习中心调整。已有有效计划时不会重复创建。</small></div>{mutation.feedback}</section>
}
