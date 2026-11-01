import type {components} from '../../api.generated'
import {ApiError} from '../../api.ts'
import type {SearchFilters} from './keyword'

export type SemanticStatus=components['schemas']['SemanticStatusOut']
export type SemanticIndex=components['schemas']['SemanticIndexOut']
export type SemanticPreview=components['schemas']['SemanticPreviewOut']
export type SemanticBuild=components['schemas']['SemanticIndexCreate']
export type SemanticQuery=components['schemas']['SemanticQueryOut']
export type SemanticRequest=components['schemas']['SemanticQueryCreate']
export const semanticLabels:Record<string,string>={queued:'排队中',building:'构建中',ready:'待激活',active:'使用中',failed:'失败',running:'检索中',succeeded:'已完成'}
// A server error can arrive after a paid operation was accepted or committed.
export function semanticAmbiguous(error:unknown):boolean {
  return !(error instanceof ApiError)||error.status>=500||error.status===408
}
export function semanticRequest(filters:SearchFilters,key:string):SemanticRequest {
  const q=filters.q.trim(),length=Array.from(q).length
  if(length<1||length>100)throw new Error('请输入 1–100 个字符的查询。')
  if(filters.kind&&!['text','image'].includes(filters.kind))throw new Error('笔记类型无效。')
  return {request_key:key,q,confirm_external:true,limit:20,...(filters.kind?{kind:filters.kind as 'text'|'image'}:{}),...(filters.folder_id?{folder_id:filters.folder_id}:{}),...(filters.notebook_id?{notebook_id:filters.notebook_id}:{}),...(filters.tag_id?{tag_id:filters.tag_id}:{})}
}
export function semanticUsage(usage:Record<string,number>):string {
  return Object.entries(usage).map(([key,value])=>`${key}: ${value}`).join(' · ')||'暂无已记录用量'
}
export function semanticReason(status:SemanticStatus):string {
  if(!status.enabled)return '语义检索未启用，仍可使用关键词搜索。'
  if(!status.configured)return '百炼文本向量服务尚未配置，仍可使用关键词搜索。'
  if(!status.available)return '语义服务暂不可用，仍可使用关键词搜索。'
  return status.active_index_id?'语义索引可用。':'尚无激活索引，请先在模型设置中预览、构建并激活。'
}
