import type {components} from '../../api.generated'
import {ApiError} from '../../api.ts'
export type Quiz=components['schemas']['QuizOut']
export type QuizList=components['schemas']['QuizList']
export type Question=components['schemas']['QuestionCreate']
export type Attempt=components['schemas']['AttemptOut']
export type AttemptList=components['schemas']['AttemptList']
export type Kind=Question['kind']
export type Generation=Omit<components['schemas']['app__modules__learning__quiz_schemas__GenerationOut'],'provider'|'model'>&{provider?:string;model:string}
export type GenerationList=Omit<components['schemas']['app__modules__learning__quiz_schemas__GenerationList'],'items'>&{items:Generation[]}
export type GenerationPreview=Omit<components['schemas']['app__modules__learning__quiz_schemas__GenerationPreviewOut'],'provider'|'model'>&{provider?:string;model:string}
export type GenerationRequest=Omit<components['schemas']['app__modules__learning__quiz_schemas__GenerationCreate'],'provider'|'model'>&{provider?:string;model:string}
export type PreviewRequest=Omit<components['schemas']['app__modules__learning__quiz_schemas__GenerationPreview'],'provider'|'model'>&{provider?:string;model:string}
export type Grading=Omit<components['schemas']['GradingOut'],'provider'|'model'>&{provider?:string;model:string}
export type GradingPreview=Omit<components['schemas']['GradingPreviewOut'],'provider'|'model'>&{provider?:string;model:string}
export const kindNames:Record<Kind,string>={single_choice:'单选题',multiple_choice:'多选题',true_false:'判断题',fill_blank:'填空题',short_answer:'简答题'}
export const statuses={draft:'草稿',confirmed:'已确认',archived:'已归档'}
export const attemptStatuses={in_progress:'作答中',submitted:'已交卷',needs_review:'待复核',graded:'已评分'}
export function newQuestion(kind:Kind='single_choice'):Question{return {kind,prompt:'',options:kind.endsWith('choice')?['','']:null,correct_answer:kind==='single_choice'?0:kind==='multiple_choice'?[0]:kind==='true_false'?true:kind==='fill_blank'?['']:'',explanation:'',max_points:1,source:null}}
export function validateQuiz(title:string,questions:Question[]){
  if(!title.trim()||Array.from(title).length>200||title.includes('\0'))throw new Error('测验标题必填，最多 200 字符。')
  if(!questions.length||questions.length>50)throw new Error('每份测验需要 1–50 道题。')
  for(const [i,q] of questions.entries()){
    const fail=(message:string)=>{throw new Error('第 '+(i+1)+' 题：'+message)}
    if(!q.prompt.trim()||Array.from(q.prompt).length>8000||q.prompt.includes('\0')||(q.explanation||'').includes('\0')||Array.from(q.explanation||'').length>8000)fail('题干必填，题干和解析最多 8000 字符。')
    if(!Number.isInteger(q.max_points)||q.max_points!<1||q.max_points!>100)fail('分数须为 1–100 的整数。')
    const a=q.correct_answer
    if(q.kind.endsWith('choice')){const o=q.options;if(!o||o.length<2||o.length>8||o.some(s=>!s.trim()||s.includes('\0')||Array.from(s).length>2000)||new Set(o.map(s=>s.trim())).size!==o.length)fail('需要 2–8 个非空、不重复选项。');const indices=q.kind==='single_choice'?[a]:a;if(!Array.isArray(indices)||!indices.length||indices.some(v=>!Number.isInteger(v)||v<0||v>=o!.length)||new Set(indices).size!==indices.length)fail('请选择有效且不重复的正确选项。')}
    else if(q.options!=null)fail('此题型不能包含选项。')
    if(q.kind==='true_false'&&typeof a!=='boolean')fail('请选择正确或错误。')
    if(q.kind==='fill_blank'){const n=q.prompt.split('{{blank}}').length-1;if(n<1||n>20||!Array.isArray(a)||a.length!==n||a.some(v=>typeof v!=='string'||!v.trim()||v.includes('\0')||Array.from(v).length>2000))fail('题干使用 {{blank}}，每个空位填写一个非空答案（最多 20 空）。')}
    if(q.kind==='short_answer'&&(typeof a!=='string'||!a.trim()||a.includes('\0')||Array.from(a).length>8000))fail('请填写最多 8000 字符的参考答案。')
  }
}
export function questionInput(q:Quiz['questions'][number]):Question{
  const s=q.source
  const source:Question['source']=!s?null:s.kind==='text'?{kind:s.kind,note_id:s.note_id,revision:s.revision,start:s.start,end:s.end,quote:s.quote}:s.kind==='image'?{kind:s.kind,note_id:s.note_id,revision:s.revision,page_id:s.pages[0].page_id,region:s.pages[0].region}: {kind:s.kind,note_id:s.note_id,revision:s.revision,mode:s.pages[0].region?'region':'pages',page_numbers:s.pages.map(p=>p.number),region:s.pages[0].region}
  return {kind:q.kind,prompt:q.prompt,options:q.options,correct_answer:q.correct_answer,explanation:q.explanation,max_points:q.max_points,source}
}
export type Pending={path:string;method:'POST'|'PUT';body:Record<string,unknown>}
export function pendingKey(owner:string,scope:string,channel:string){return 'ai-notes:quiz-pending:v1:'+owner+':'+scope+':'+channel}
export function ambiguous(error:unknown){return !(error instanceof ApiError)||error.status>=500||error.status===408}
export function reviewQuestion(attempt:Attempt,answerId:string){return attempt.answers.find(a=>a.id===answerId)?.question_id}
