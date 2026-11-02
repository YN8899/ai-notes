export type PostType='note'|'question'|'experience'|'resource'
export interface Author {id:string;username:string;display_name:string}
export interface Page<T>{items:T[];next_cursor:string|null;limit:number}
export interface Summary {id:string;author:Author;title:string;subject:string;post_type:PostType;tags:string[];excerpt:string;published_at:string;snapshot_version:number;like_count:number;comment_count:number;liked:boolean;bookmarked:boolean;recommendation_reason:string}
export interface PublicPost extends Summary {body_json:Record<string,unknown>;plain_text:string;source_revision:number|null;assets:{id:string;position:number;filename:string;width:number|null;height:number|null;image_url:string;original_url:string}[]}
export interface Content {title:string;body_json:Record<string,unknown>;plain_text:string;subject:string;post_type:PostType;tags:string[];source_note_id:string|null;source_revision:number|null}
export interface Draft extends Content {id:string;revision:number;status:'draft'|'published'|'withdrawn'|'hidden';live_snapshot_id:string|null;preparing_snapshot_id:string|null;publish_status:'preparing'|'ready'|'failed'|'cancelled'|null;publish_error:string|null;moderation_reason:string|null;created_at:string;updated_at:string}
export interface Comment {id:string;post_id:string;author:Author;body:string;created_at:string}
export interface Report {id:string;post_id:string;reason:string;status:'pending'|'dismissed'|'hidden';resolution:string|null;created_at:string;resolved_at:string|null}
export const postTypes:Record<PostType,string>={note:'学习笔记',question:'问题讨论',experience:'学习经验',resource:'学习资源'}
export const statuses:Record<Draft['status'],string>={draft:'草稿',published:'已发布',withdrawn:'已撤回',hidden:'已下架'}
export const emptyContent=():Content=>({title:'',body_json:{},plain_text:'',subject:'',post_type:'note',tags:[],source_note_id:null,source_revision:null})
export const contentOf=(draft:Content):Content=>({title:draft.title,body_json:draft.body_json,plain_text:draft.plain_text,subject:draft.subject,post_type:draft.post_type,tags:[...draft.tags],source_note_id:draft.source_note_id,source_revision:draft.source_revision})
export function parseTags(value:string){const tags=value.split(/[,，]/).map(v=>v.trim()).filter(Boolean);if(tags.length>12)throw new Error('最多填写12个标签');if(tags.some(v=>v.length>32))throw new Error('每个标签最多32个字符');if(new Set(tags).size!==tags.length)throw new Error('标签不能重复');return tags}
export function editorBody(body:Record<string,unknown>,plain:string):Record<string,unknown>{return Object.keys(body).length||!plain?body:{type:'doc',content:plain.split('\n').map(text=>({type:'paragraph',...(text?{content:[{type:'text',text}]}:{})}))}}
export function publicAssetPath(value:string){const path=value.replace(/^\/api\/v1/,'');if(!/^\/community\/posts\/[\da-f-]+\/assets\/[\da-f-]+(?:\?kind=(?:original|reading))?$/.test(path))throw new Error('社区附件地址无效');return path}
