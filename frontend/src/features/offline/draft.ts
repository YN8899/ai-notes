import {equal,threeWay} from './merge.ts'
import type {Json,Note} from './types.ts'
export type NoteDraft=Pick<Note,'title'|'body_json'|'plain_text'|'notebook_id'|'tag_ids'>
export const noteDraft=(note:Note):NoteDraft=>structuredClone({title:note.title,body_json:note.body_json,plain_text:note.plain_text,notebook_id:note.notebook_id,tag_ids:note.tag_ids})
export class LocalDraftConflict extends Error{
  readonly fields:string[]
  readonly current:Json
  constructor(fields:string[],current:Json){super(`本机编辑冲突：${fields.join('、')} 已在其他视图修改。界面草稿仍保留，请比较后明确重载，不会自动覆盖。`);this.fields=fields;this.current=structuredClone(current)}
}
export function checkExpected(current:Json,expected:Json,payload:Json){
  const found=threeWay(expected,{...expected,...payload},current,Object.keys(payload)).conflicts
  if('deleted_at'in expected&&!equal(current.deleted_at,expected.deleted_at))found.push('deleted_at')
  if(found.length)throw new LocalDraftConflict([...new Set(found)],current)
}
export function changedNoteFields(expected:NoteDraft,draft:NoteDraft):Json{
  const patch:Json={}
  for(const key of ['title','notebook_id','tag_ids'] as const)if(!equal(expected[key],draft[key]))patch[key]=structuredClone(draft[key])
  if(!equal(expected.body_json,draft.body_json)||!equal(expected.plain_text,draft.plain_text)){patch.body_json=structuredClone(draft.body_json);patch.plain_text=draft.plain_text}
  return patch
}
