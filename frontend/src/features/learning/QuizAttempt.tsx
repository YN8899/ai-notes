import {useCallback,useEffect,useRef,useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {api} from '../../api'
import {readSession} from '../../session'
import {createUuid} from '../../uuid'
import {NoteError} from '../notes/NoteError'
import {QuizSource} from './QuizSource'
import {QuizAiGrading} from './QuizAI'
import {useQuizMutation} from './QuizMutation'
import {useLearningActivity} from './ActivityTracker'
import {attemptStatuses,kindNames,pendingKey,reviewQuestion,type Attempt} from './quizzes'
type Drafts=Record<string,unknown>
export function QuizAttempt({owner,sessionId,id,onClose,onOpenNote}:{owner:string;sessionId:string;id:string;onClose:()=>void;onOpenNote:(id:string)=>void}){
  const storageKey=pendingKey(owner,readSession()?.scope||sessionId,'draft:'+id),[attempt,setAttempt]=useState<Attempt|null>(null),[drafts,setDrafts]=useState<Drafts>(()=>{try{return JSON.parse(sessionStorage.getItem(storageKey)||'{}')}catch{return {}}}),[armed,setArmed]=useState(false),[storageError,setStorageError]=useState<unknown>(null),[grading,setGrading]=useState(''),[submitConfirm,setSubmitConfirm]=useState(false),[source,setSource]=useState(''),draftRef=useRef(drafts),[pause,setPause]=useState(false)
  draftRef.current=drafts
  const query=useQuery({queryKey:['quiz-attempt',owner,sessionId,id],queryFn:()=>api<Attempt>('/learning/quiz-attempts/'+id),retry:false,refetchOnWindowFocus:false,refetchOnMount:'always'})
  useLearningActivity('quiz',attempt&&!query.isError?{quiz_id:attempt.quiz_id}:null)
  const mutation=useQuizMutation<Attempt>(owner,sessionId,'attempt:'+id,(data,pending,retry)=>{
    setAttempt(old=>old&&old.revision>data.revision?old:data)
    const qid=pending.method==='PUT'?pending.path.split('/').at(-1):null
    if(qid)setDrafts(old=>{if(JSON.stringify(old[qid])!==JSON.stringify(pending.body.answer))return old;const next={...old};delete next[qid];return next})
    setSubmitConfirm(false)
    // Operation replays return the original receipt, which may be older than GET.
    if(retry){setArmed(false);void refresh().catch(e=>setStorageError(e))}
  })
  // A start replay is an immutable receipt; even cached attempt DTOs must be reread.
  useEffect(()=>{if(query.data&&!query.isFetching&&query.isFetchedAfterMount&&!query.isError&&!attempt)setAttempt(query.data)},[query.data,query.isFetching,query.isFetchedAfterMount,query.isError,attempt])
  useEffect(()=>{if(!mutation.active())return;try{if(Object.keys(drafts).length)sessionStorage.setItem(storageKey,JSON.stringify(drafts));else sessionStorage.removeItem(storageKey);setStorageError(null)}catch{setStorageError(new Error('浏览器无法保存本地输入。请保留本页并允许标签页存储，尚未自动写入。'));setArmed(false)}},[drafts,storageKey])
  const refresh=useCallback(async()=>{const value=await query.refetch();if(value.error)throw value.error;if(mutation.active()&&value.data){setAttempt(value.data);setArmed(false);setPause(false)}return value.data},[query.refetch])
  const dirty=Object.keys(drafts).length
  useEffect(()=>{if(query.isFetching||!armed||pause||!attempt||attempt.status!=='in_progress'||mutation.blocked||storageError||!dirty)return;const timer=setTimeout(()=>{
    const qid=Object.keys(draftRef.current)[0];if(!qid)return;void mutation.send({path:'/learning/quiz-attempts/'+id+'/answers/'+qid,method:'PUT',body:{revision:attempt.revision,request_key:createUuid(),answer:draftRef.current[qid]}})
  },500);return()=>clearTimeout(timer)},[query.isFetching,armed,pause,attempt,mutation.blocked,drafts,dirty,storageError])
  function change(qid:string,value:unknown){setDrafts(old=>({...old,[qid]:value}));setArmed(true);setSubmitConfirm(false)}
  return <section className="panel quiz-attempt" aria-label="测验作答"><div className="panel-heading"><h3>{attempt?.title||'读取作答'}</h3><button type="button" className="text-button" onClick={onClose}>关闭作答（保留未保存输入）</button></div><NoteError error={query.error||storageError}/>{query.isError&&!attempt?<button type="button" className="secondary" disabled={query.isFetching} onClick={()=>void query.refetch()}>重试读取作答</button>:null}{query.isPending?<p>正在读取作答快照…</p>:null}{attempt?<><p className="muted">测验快照版本 {attempt.quiz_revision} · 作答版本 {attempt.revision} · {attemptStatuses[attempt.status]}</p><p>填空按每空去首尾空白并忽略大小写，所有空全对才得分；多选要求集合完全相等。简答需要人工评分或明确请求 AI 建议。</p><div className="card-actions"><button type="button" className="secondary" disabled={mutation.busy||query.isFetching} onClick={()=>void mutation.reconcile(refresh)}>读取服务器作答（保留本地输入）</button>{dirty&&attempt.status==='in_progress'?<button type="button" className="secondary" disabled={query.isFetching||mutation.blocked||!!storageError||attempt.status!=='in_progress'} onClick={()=>{setArmed(true);setPause(false)}}>继续保存本地输入（{dirty} 题）</button>:null}{armed&&dirty?<button type="button" className="text-button" onClick={()=>setPause(true)}>暂停自动保存</button>:null}<span role="status">{mutation.busy?'正在串行保存…':dirty?dirty+(attempt.status==='in_progress'?' 题本地输入待保存':' 题未提交本地草稿保留，未计入评分'):'答案已保存'}</span></div>{mutation.feedback}
    {attempt.questions.map((q,index)=>{
      const answer=attempt.answers.find(a=>a.question_id===q.id),finished=attempt.status!=='in_progress',hasDraft=Object.hasOwn(drafts,q.id),value=!finished&&hasDraft?drafts[q.id]:answer?.answer
      return <section className="quiz-question" key={q.id} aria-label={'作答第 '+(index+1)+' 题'}><h4>{index+1}. {kindNames[q.kind]} · 满分 {q.max_points}</h4><p className="quiz-copy">{q.prompt}</p>{finished?<><p>我的答案：{displayAnswer(q,value)}</p>{hasDraft?<aside className="quiz-warning" aria-label="未提交本地草稿"><strong>本地未提交草稿（未计入本次评分）</strong><p>{displayAnswer(q,drafts[q.id])}</p><p>服务端已交卷，以上保留内容没有提交。本题分数仅对应上方服务器已交卷答案。</p></aside>:null}<p>参考答案：{displayAnswer(q,q.correct_answer)}</p><p>解析 / 评分标准：{q.explanation||'无'}</p><p>{answer?.points===null?'待复核':(answer?.points??0)+' / '+q.max_points+' 分'} · {gradeStatuses[answer?.status||'']||'未作答'} · 来源：{provenance[answer?.provenance||'']||'未评分'}</p><p>{answer?.feedback}</p><ManualReview key={q.id} max={q.max_points} initial={answer?.points??0} disabled={mutation.blocked||grading!==''} onReview={(points,reason,feedback)=>void mutation.send({path:'/learning/quiz-attempts/'+id+'/answers/'+q.id+'/reviews',method:'POST',body:{revision:attempt.revision,request_key:createUuid(),points,reason,feedback}})}/>{q.kind==='short_answer'&&answer?<button type="button" className="secondary" disabled={mutation.blocked} onClick={()=>setGrading(v=>v===q.id?'':q.id)}>{grading===q.id?'收起 AI 评分':'打开简答 AI 评分'}</button>:null}</>:<AnswerInput question={q} value={value} onChange={v=>change(q.id,v)}/>}{q.source?<button type="button" className="text-button" onClick={()=>setSource(v=>v===q.id?'':q.id)}>{source===q.id?'收起来源':'查看题目历史来源'}</button>:null}{source===q.id?<QuizSource owner={owner} sessionId={sessionId} path={'/learning/quiz-attempts/'+id+'/questions/'+q.id+'/source'} onOpenNote={onOpenNote}/>:null}</section>
    })}
    {grading?<QuizAiGrading key={attempt.id} owner={owner} sessionId={sessionId} attempt={attempt} questionId={grading} onRefresh={refresh} onOpenQuestion={setGrading} disabled={mutation.blocked}/>:null}
    {attempt.status==='in_progress'?<div className="quiz-submit"><p>交卷后无法更改答案。未答题记 0 分；简答可能显示待复核。评分不会自动改动复习计划。</p><label className="card-check"><input type="checkbox" disabled={!!dirty||mutation.blocked} checked={submitConfirm} onChange={e=>setSubmitConfirm(e.target.checked)}/>我已检查所有答案，确认正式交卷。</label><button type="button" className="primary" disabled={!submitConfirm||!!dirty||mutation.blocked} onClick={()=>void mutation.send({path:'/learning/quiz-attempts/'+id+'/submit',method:'POST',body:{revision:attempt.revision,request_key:createUuid()}})}>正式交卷并评分</button></div>:<><h3>总分 {attempt.total_points} / {attempt.max_points} · {attemptStatuses[attempt.status]}</h3><section aria-label="不可变人工复核历史"><h4>人工复核历史</h4>{attempt.reviews.length?<ol>{attempt.reviews.map(r=>{const qid=reviewQuestion(attempt,r.answer_id),index=attempt.questions.findIndex(q=>q.id===qid);return <li key={r.id}><strong>{index<0?'未知题目（原记录保留）':'第 '+(index+1)+' 题'}</strong><p>原评分 {gradeDescription(r.old_grade)} → 新评分 {gradeDescription(r.new_grade)}</p><p>原因：{r.reason} · {r.created_at}</p></li>})}</ol>:<p>暂无人工复核。</p>}</section></>}
  </>:null}</section>
}
function displayAnswer(q:Attempt['questions'][number],value:unknown):string{if(value===null||value===undefined)return '未作答';if(q.kind==='single_choice'&&typeof value==='number')return q.options?.[value]||String(value);if(q.kind==='multiple_choice'&&Array.isArray(value))return value.map(v=>q.options?.[v]||String(v)).join('、');if(q.kind==='true_false')return value===true?'正确':value===false?'错误':'未作答';return Array.isArray(value)?value.join(' / '):String(value)}
function AnswerInput({question:q,value,onChange}:{question:Attempt['questions'][number];value:unknown;onChange:(value:unknown)=>void}){
  if(q.kind.endsWith('choice'))return <fieldset><legend>选择你的答案</legend>{q.options?.map((o,i)=><label className="quiz-answer-option" key={i}><input type={q.kind==='single_choice'?'radio':'checkbox'} name={'answer-'+q.id} checked={q.kind==='single_choice'?value===i:Array.isArray(value)&&value.includes(i)} onChange={e=>onChange(q.kind==='single_choice'?i:e.target.checked?[...(Array.isArray(value)?value:[]),i]:(Array.isArray(value)?value:[]).filter(n=>n!==i))}/>{o}</label>)}</fieldset>
  if(q.kind==='true_false')return <fieldset><legend>判断</legend>{[true,false].map(v=><label className="quiz-answer-option" key={String(v)}><input type="radio" name={'answer-'+q.id} checked={value===v} onChange={()=>onChange(v)}/>{v?'正确':'错误'}</label>)}</fieldset>
  if(q.kind==='fill_blank')return <div>{Array.from({length:q.prompt.split('{{blank}}').length-1},(_,i)=><label key={i}>第 {i+1} 空<input maxLength={2000} value={Array.isArray(value)?value[i]||'':''} onChange={e=>onChange(Array.from({length:q.prompt.split('{{blank}}').length-1},(_,n)=>n===i?e.target.value:Array.isArray(value)?value[n]||'':''))}/></label>)}</div>
  return <label>我的简答<textarea aria-label="我的简答" maxLength={8000} rows={5} value={typeof value==='string'?value:''} onChange={e=>onChange(e.target.value)}/></label>
}
function ManualReview({max,initial,disabled,onReview}:{max:number;initial:number;disabled:boolean;onReview:(points:number,reason:string,feedback:string)=>void}){
  const [points,setPoints]=useState(initial),[reason,setReason]=useState(''),[feedback,setFeedback]=useState('')
  return <form className="quiz-manual" onSubmit={e=>{e.preventDefault();if(Number.isInteger(points)&&points>=0&&points<=max&&reason.trim())onReview(points,reason,feedback)}}><fieldset disabled={disabled}><legend>人工复核（保留历史）</legend><label>复核分数<input type="number" min={0} max={max} required value={points} onChange={e=>setPoints(Number(e.target.value))}/></label><label>复核原因<input required maxLength={2000} value={reason} onChange={e=>setReason(e.target.value)}/></label><label>反馈<textarea aria-label="反馈" maxLength={8000} value={feedback} onChange={e=>setFeedback(e.target.value)}/></label><button type="submit" className="secondary" disabled={!reason.trim()}>保存人工复核</button></fieldset></form>
}

const gradeStatuses:Record<string,string>={ungraded:'未评分',graded:'已评分',needs_review:'待复核'}
const provenance:Record<string,string>={local:'本地规则',deterministic:'确定规则',manual:'人工复核',ai:'AI 评分',ai_suggestion:'AI 建议待复核'}
function gradeDescription(value:Record<string,unknown>){return (value.points==null?'待评分':String(value.points)+' 分')+' · '+(provenance[String(value.provenance)]||'未评分')+(value.feedback?' · '+String(value.feedback):'')}
