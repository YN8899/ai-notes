import type {components} from '../../api.generated'
import {ApiError} from '../../api.ts'
export type ReviewPlan=components['schemas']['ReviewPlanOut']
export type ReviewPlans=components['schemas']['ReviewPlanList']
export type ReviewLogs=components['schemas']['ReviewLogList']
export type ReviewLog=components['schemas']['ReviewLogOut']
export type Receipt=components['schemas']['ReviewReceipt']
export type ReviewSubmit=components['schemas']['ReviewSubmit']
export type ReviewPatch=components['schemas']['ReviewPlanPatch']
export type ReviewPractice=components['schemas']['ReviewPractice']
export type ReviewCreate=components['schemas']['ReviewPlanCreate']
export const reviewKinds={note:'笔记',card:'卡片',quiz:'测验'}
export const reviewStatuses={active:'进行中',paused:'已暂停',archived:'已归档'}
export const ratings=['忘记','困难','良好','轻松']
export const actions:Record<string,string>={create:'加入复习',review:'正式评价',practice:'临时练习',reschedule:'修改时间',switch:'切换策略',pause:'暂停',resume:'恢复',archive:'归档'}
export const deviceZone=()=>Intl.DateTimeFormat().resolvedOptions().timeZone
export function reviewTime(value:unknown,zone='Asia/Shanghai'){if(typeof value!=='string'||!Number.isFinite(Date.parse(value)))return '—';return new Intl.DateTimeFormat('zh-CN',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))}
/** datetime-local belongs to the device's zone. Never append Z to its text. */
export function localToUtc(value:string,future=false,now=Date.now()) {const time=new Date(value).getTime();if(!value||!Number.isFinite(time))throw new Error('请选择有效时间。');if(future&&time<=now)throw new Error('下次复习时间必须晚于当前时间。');return new Date(time).toISOString()}
export function formalPayload(plan:ReviewPlan,rating:number,key:string,time:string,reason:string):ReviewSubmit {if(!Number.isInteger(rating)||rating<1||rating>4)throw new Error('请选择四档评价。');const base={request_key:key,revision:plan.revision,rating};if(plan.strategy==='fsrs')return base;if(!reason.trim())throw new Error('手动排期需要填写原因。');return {...base,next_due_at:localToUtc(time,true),reason:reason.trim()}}
export function practicePayload(plan:ReviewPlan,rating:number,key:string):ReviewPractice {return {kind:plan.item.kind,[plan.item.kind+'_id']:plan.item[`${plan.item.kind}_id`],plan_id:plan.id,revision:plan.revision,rating,request_key:key}}
export type ReviewFilters={mode:'due'|'all';kind:string;status:string;eligible:boolean;from:string;to:string;offset:number}
export function reviewListPath(filters:ReviewFilters){const p=new URLSearchParams({offset:String(filters.offset),limit:'50'});if(filters.kind)p.set('kind',filters.kind);if(filters.mode==='all'){if(filters.status)p.set('status',filters.status);p.set('eligible_only',String(filters.eligible))}const low=filters.from?localToUtc(filters.from):'',high=filters.to?localToUtc(filters.to):'';if(low&&high&&low>high)throw new Error('开始时间不能晚于结束时间。');if(low)p.set('due_from',low);if(high)p.set('due_to',high);return '/learning/'+(filters.mode==='due'?'review-due':'review-plans')+'?'+p}
export function ambiguousReview(error:unknown){return !(error instanceof ApiError)||error.status>=500||error.status===408}
export type ReviewAttempt={path:string;method:'POST'|'PATCH';body:ReviewSubmit|ReviewPatch|ReviewPractice|ReviewCreate;planId?:string;itemId?:string}
export function attemptMatches(value:unknown):value is ReviewAttempt {if(!value||typeof value!=='object')return false;const a=value as ReviewAttempt;return /^\/learning\/(review-plans(?:\/[a-zA-Z0-9-]+(?:\/reviews)?)?|review-items\/practice)$/.test(a.path)&&['POST','PATCH'].includes(a.method)&&typeof a.body?.request_key==='string'&&/^[0-9a-f-]{36}$/i.test(a.body.request_key)}
export function snapshotDescription(snapshot:ReviewLog['old_snapshot'],zone='Asia/Shanghai'){if(!snapshot)return '无原排期';return `${snapshot.strategy==='fsrs'?'FSRS 自动排期':'手动排期'} · ${reviewTime(snapshot.next_due_at,zone)}${typeof snapshot.status==='string'?' · '+(reviewStatuses[snapshot.status as keyof typeof reviewStatuses]||snapshot.status):''}`}
export function reviewReason(reason:string){const system:Record<string,string>={card_confirmed:'卡片确认后自动建立独立计划',card_archived:'卡片归档，计划已暂停',deck_archived:'卡片集归档，计划已暂停',card_draft:'卡片退回草稿，计划已暂停'};return (system[reason]||reason).replace(/\s*\[fsrs_mode=(resume_last|restart|none)\]/g,(_,mode)=>mode==='resume_last'?'（恢复上次算法状态）':mode==='restart'?'（重新开始，保留历史）':'')}
/** A restored request can belong to another object in this owner's tab. */
export function matchingReviewPlan(plan:ReviewPlan|null,kind:ReviewPlan['item']['kind'],id:string):ReviewPlan|null{return plan?.item.kind===kind&&plan.item[`${kind}_id`]===id?plan:null}
