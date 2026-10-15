import type { components } from '../../api.generated'
export type Folder=components['schemas']['FolderOut']
export type Notebook=components['schemas']['NotebookOut']
export type Tag=components['schemas']['TagOut']
export type NoteSummary=components['schemas']['NoteSummary']
export type Note=components['schemas']['NoteOut']
export type NoteList=components['schemas']['NoteList']
export type Version=components['schemas']['VersionOut']
export type VersionList=components['schemas']['VersionList']
export interface Content { title:string; notebook_id:string|null; tag_ids:string[]; body_json:Record<string,unknown>; plain_text:string }
export const noteContent=(note:Note):Content=>({title:note.title,notebook_id:note.notebook_id,tag_ids:note.tag_ids,body_json:note.body_json,plain_text:note.plain_text})
