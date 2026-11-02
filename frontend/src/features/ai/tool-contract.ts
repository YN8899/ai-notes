export type ToolName='search_notes'|'read_note_excerpt'|'learning_summary'
export type LearningDates={date_from:string;date_to:string}
export type ToolScope={note_ids:string[];notebook_ids:string[];learning_summary?:LearningDates|null}
export type ToolSelection={tools:ToolName[];tool_scope:ToolScope|null}
export type NoteSource={note_id:string;revision:number;title:string;start?:number;end?:number}
export type LearningSource=LearningDates&{kind:'learning_summary';timezone:string;fingerprint:string}
export type ToolSource=NoteSource|LearningSource
export type FrozenScope={version:1;notes:NoteSource[];learning_summary?:LearningSource}
export type ToolPreview={tools:ToolName[];scope:FrozenScope|null;note_count:number;preview_hash:string}
export type ToolHistory={provider:string;model:string;tools:string[];scope:FrozenScope|null;calls:{id:string;position:number;provider_call_id:string;tool_name:string;arguments:Record<string,unknown>;result:string|null;sources:ToolSource[];status:'pending'|'succeeded'|'failed'|'interrupted';error_code:string|null;created_at:string;finished_at:string|null}[];attempts:{id:string;position:number;kind:string;status:string;usage:Record<string,number>|null;error_code:string|null;created_at:string}[]}
export const emptyTools:ToolSelection={tools:[],tool_scope:null}
export function validLearningDates(value:LearningDates){
  const parse=(s:string)=>{const time=Date.parse(s+'T00:00:00Z');return /^\d{4}-\d{2}-\d{2}$/.test(s)&&s>='1900-01-01'&&s<'9999-01-01'&&Number.isFinite(time)&&new Date(time).toISOString().slice(0,10)===s?time:NaN}
  const from=parse(value.date_from),to=parse(value.date_to)
  return Number.isFinite(from)&&Number.isFinite(to)&&to>=from&&(to-from)/86400000<93
}
export function validateTools(value:ToolSelection){
  if(new Set(value.tools).size!==value.tools.length||value.tools.some(t=>!['search_notes','read_note_excerpt','learning_summary'].includes(t)))throw new Error('工具选择无效。')
  if(!value.tools.length){if(value.tool_scope!==null)throw new Error('关闭工具后须清空范围。');return}
  const s=value.tool_scope;if(!s)throw new Error('请明确选择工具授权范围。')
  const notes=value.tools.some(t=>t!=='learning_summary'),summary=value.tools.includes('learning_summary')
  const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
  if(s.note_ids.length>100||s.notebook_ids.length>20||[s.note_ids,s.notebook_ids].some(ids=>new Set(ids).size!==ids.length||ids.some(id=>!uuid.test(id))))throw new Error('范围编号无效或超过数量上限。')
  if(notes!==!!(s.note_ids.length||s.notebook_ids.length))throw new Error('笔记工具须明确选择本人文字笔记或纯文字笔记本。')
  if(summary!==!!s.learning_summary||s.learning_summary&&!validLearningDates(s.learning_summary))throw new Error('学习汇总须明确选择连续 1–93 天的有效日期。')
}
export function previewMatches(value:ToolSelection,preview:ToolPreview){
  validateTools(value)
  return /^[0-9a-f]{64}$/.test(preview.preview_hash)&&JSON.stringify(value.tools)===JSON.stringify(preview.tools)&&preview.note_count>=0&&preview.note_count<=100&&preview.scope!==null&&preview.scope.notes.length===preview.note_count&&value.tool_scope!.note_ids.every(id=>preview.scope!.notes.some(n=>n.note_id===id))&&(!value.tool_scope!.learning_summary||preview.scope.learning_summary?.date_from===value.tool_scope!.learning_summary.date_from&&preview.scope.learning_summary.date_to===value.tool_scope!.learning_summary.date_to)
}
