import type {AnalysisSubmission} from './types'
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export function submissionStorageKey(owner:string,sessionScope:string){if(!owner||!sessionScope)throw new Error('缺少稳定会话身份，不能保存分析提交');return 'ai-notes:analysis-submission:'+owner+':'+sessionScope}
export const submissionStorageUnavailable='无法读取或保存当前标签页的原请求标识，已禁止新的模型提交。事实和历史仍可读取；请恢复标签页存储后重新打开本页。'
export function restoreSubmission(owner:string,readScope:()=>string|undefined,storage:()=>Pick<Storage,'getItem'>){
  try{
    const scope=readScope()
    if(typeof scope!=='string'||!scope.trim()||scope.trim()!==scope||scope.length>256||/[\u0000-\u001f]/.test(scope))throw new Error('missing_session_scope')
    const key=submissionStorageKey(owner,scope),raw=storage().getItem(key),pending=parseSubmission(raw)
    if(raw!==null&&!pending)throw new Error('invalid_pending_submission')
    return {scope,key,pending,error:''}
  }catch{return {scope:null,key:null,pending:null,error:submissionStorageUnavailable}}
}
export function parseSubmission(raw:string|null):AnalysisSubmission|null{if(!raw)return null;try{const v:unknown=JSON.parse(raw);if(!v||typeof v!=='object')return null;const x=v as Record<string,unknown>;if(Object.keys(x).sort().join(',')!=='preview_hash,preview_id,request_key'||typeof x.preview_id!=='string'||typeof x.request_key!=='string'||typeof x.preview_hash!=='string'||!uuid.test(x.preview_id)||!uuid.test(x.request_key)||!(/^[0-9a-f]{64}$/.test(x.preview_hash)))return null;return {preview_id:x.preview_id,preview_hash:x.preview_hash,request_key:x.request_key}}catch{return null}}
