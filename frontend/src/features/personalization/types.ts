import type {Page,Summary} from '../community/types'
export const tileIds=['notes','reviews','social','messages','shares'] as const
export type Tile=typeof tileIds[number]
export const sectionLabels={home:'空间概览',notes:'我的笔记',cards:'学习卡片',quizzes:'学习测验',reviews:'复习中心',learning:'学习统计',preferences:'学习提醒',social:'好友与分享',messages:'私聊消息',community:'学习社区',profile:'个人设置',chat:'AI 对话'} as const
export type HomeSection=keyof typeof sectionLabels
export interface Preferences {tile_order:Tile[];hidden_tiles:Tile[];default_section:HomeSection;recommendations_enabled:boolean;learning_signals_enabled:boolean;subjects:string[];tags:string[];ai_analysis_enabled:boolean}
export interface PreferencesOut extends Preferences {revision:number;updated_at:string|null}
export interface RecommendationPage extends Page<Summary> {mode:'latest'|'personalized';reasons_used:string[];signals_fingerprint:string|null}
export interface LearningSummary {
  source:{kind:'learning_summary';date_from:string;date_to:string;timezone:string;fingerprint:string}
  facts:{activity:{total_seconds:number;reading_seconds:number;review_seconds:number;practice_seconds:number;quiz_seconds:number};reviews:{formal_reviews:number;practice_count:number;daily_distinct_review_object_sum:number};quiz:{submitted:number;graded:number;needs_review:number;actual_points:number;max_points:number;manually_corrected:number};reading_progress:{recorded_notes:number;active_notes_read:number;active_notes_total:number}}
  sample:{insufficient:boolean;reason:string|null};interpretation:'descriptive_facts_not_mastery'
}
export interface AnalysisFacts {summary:LearningSummary;rules:{kind:string;rule?:string;insufficient?:boolean;text:string}[];ai_optional:boolean}
export interface AnalysisPreview {id:string;preview_hash:string;summary:LearningSummary;provider:string;model:string;input_messages:{role:string;content:string}[];input_bytes:number;expires_at:string;planned_calls:number;tools:string[]}
export interface AnalysisSubmission {preview_id:string;preview_hash:string;request_key:string}
export interface Analysis {id:string;run_id:string;summary:LearningSummary;preview_hash:string;provider:string;model:string;status:string;answer:string;usage:Record<string,unknown>|null;error_code:string|null;created_at:string;finished_at:string|null}
