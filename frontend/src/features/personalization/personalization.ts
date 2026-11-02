import {sectionLabels,type HomeSection,type Tile,type PreferencesOut} from './types.ts'
export function homeSection(value:unknown):HomeSection|null{return typeof value==='string'&&Object.hasOwn(sectionLabels,value)?value as HomeSection:null}
export function shouldApplyHomeDefault(input:{explicitNavigation:boolean;restoredNotes:boolean;restoredChat:boolean;alreadyApplied:boolean}){return !input.explicitNavigation&&!input.restoredNotes&&!input.restoredChat&&!input.alreadyApplied}
export function interestTerms(value:string,kind:'subjects'|'tags'){const terms=value.split(/[,，\n]/).map(s=>s.trim()).filter(Boolean);if(terms.length>20)throw new Error('每类最多20项兴趣');if(new Set(terms).size!==terms.length)throw new Error('兴趣不能重复');const max=kind==='subjects'?80:64;if(terms.some(s=>s.length>max||/[\u0000-\u001f]/.test(s)))throw new Error(`每项最多${max}个字符，不能含控制字符`);return terms}
export function moveTile(order:Tile[],index:number,delta:number){const next=[...order],target=index+delta;if(target<0||target>=next.length)return next;[next[index],next[target]]=[next[target],next[index]];return next}
export function preferenceBody(value:PreferencesOut){const {revision,updated_at,...body}=value;return {...body,revision}}
export function analysisActive(status:string){return status==='queued'||status==='running'}
export function unknownSubmission(error:unknown){return !(error&&typeof error==='object'&&'status' in error&&typeof error.status==='number'&&error.status<500&&error.status!==408)}
