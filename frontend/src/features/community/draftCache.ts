import {authSession} from '../../session.ts'
import type {Content,Draft} from './types'

export interface EditingDraft {base:Draft|null;content:Content;tags:string}
// Keep unsaved edits across app navigation, within this tab and login only.
const drafts=new Map<string,EditingDraft>()
let generation=authSession.snapshot().generation
authSession.subscribe(()=>{const current=authSession.snapshot();if(current.generation!==generation){drafts.clear();generation=current.generation}})
const key=(owner:string,id:string)=>owner+':'+id
export function readEditingDraft(owner:string,id:string){return authSession.current(authSession.ticket(),owner)?drafts.get(key(owner,id)):undefined}
export function writeEditingDraft(owner:string,id:string,value:EditingDraft){if(authSession.current(authSession.ticket(),owner))drafts.set(key(owner,id),value)}
export function clearEditingDraft(owner:string,id:string){drafts.delete(key(owner,id))}
