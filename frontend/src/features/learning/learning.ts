import type {components} from '../../api.generated'
export type LearningSettings=components['schemas']['LearningSettingsOut']
export type DigestList=components['schemas']['DailyDigestList']
type Complete<T>=T extends (infer V)[]?Complete<V>[]:T extends object?{[K in keyof T]-?:Complete<NonNullable<T[K]>>}:T
// Response models always serialize server defaults; generated input-style optionality is narrowed here.
export type Stats=Complete<components['schemas']['StatsOut']>
export type Calendar=Complete<components['schemas']['CalendarOut']>
export function localDay(timeZone:string,now=new Date()){const parts=new Intl.DateTimeFormat('en-CA',{timeZone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now);const get=(key:string)=>parts.find(p=>p.type===key)?.value;return `${get('year')}-${get('month')}-${get('day')}`}
export function dayShift(day:string,offset:number){const d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+offset);return d.toISOString().slice(0,10)}
export function rangeQuery(from:string,to:string){const valid=(s:string)=>/^\d{4}-\d{2}-\d{2}$/.test(s)&&!Number.isNaN(Date.parse(s))&&new Date(s).toISOString().slice(0,10)===s;if(!valid(from)||!valid(to))throw new Error('请选择有效的开始与结束日期。');const days=(Date.parse(to)-Date.parse(from))/86400000+1;if(days<1)throw new Error('开始日期不能晚于结束日期。');if(days>93)throw new Error('每次最多查询93天（含首尾），请缩短范围。');return new URLSearchParams({date_from:from,date_to:to})}
export function duration(seconds:number){return seconds<60?`${Math.round(seconds)} 秒`:`${Math.floor(seconds/60)} 分 ${Math.round(seconds%60)} 秒`}
export function readingCoverage(progress:Stats['reading_progress']|undefined){if(!progress)return null;const {recorded_notes,active_notes_read,active_notes_total}=progress;if(![recorded_notes,active_notes_read,active_notes_total].every(value=>Number.isInteger(value)&&value>=0)||active_notes_read>active_notes_total||active_notes_read>recorded_notes)return null;return {coverage:`${active_notes_read} / ${active_notes_total} 篇`,recorded:`${recorded_notes} 篇`}}
export function validSettings(value:Pick<LearningSettings,'timezone'|'review_time'>){try{new Intl.DateTimeFormat('zh-CN',{timeZone:value.timezone})}catch{throw new Error('请输入有效的 IANA 时区，例如 Asia/Shanghai。')}if(!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value.review_time))throw new Error('提醒时间须为 HH:MM。')}
