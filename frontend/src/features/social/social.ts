export interface Member {id:string;username:string;display_name:string;relationship:'self'|'friend'|'incoming'|'outgoing'|'none';following:boolean}
export interface Profile extends Member {bio:string|null;subject:string|null;details:string|null;visibility:'friends'|'private'|null;revision:number|null;details_visible:boolean}
export interface FriendRequest {id:string;member:Member;requester_id:string;status:string;revision:number;created_at:string;updated_at:string}
export interface Page<T> {items:T[];next_cursor:string|null;limit?:number}
export interface Share {id:string;note_id:string;owner:Member;recipient:Member;title:string;kind:'text'|'image';source_revision:number;revision:number;created_at:string;revoked_at:string|null}
export interface SharedNote {grant:Share;body_json:Record<string,unknown>;plain_text:string;pages:{id:string;position:number;revision:number;filename:string;width:number|null;height:number|null;status:string;image_url:string}[]}
export interface Thread {id:string;peer_id:string;peer_name:string;peer_username:string;can_send:boolean;last_seq:number;last_read_seq:number;peer_read_seq:number;unread:number;created_at:string}
export interface Attachment {id:string;filename:string;mime:string;bytes:number}
export interface Message {id:string;thread_id:string;sender_id:string;client_message_id:string;seq:number;body:string;created_at:string;attachments:Attachment[]}
export interface MessagePage {items:Message[];next_after:number;has_more:boolean}
export const queryString=(values:Record<string,string|number|undefined>)=>{const params=new URLSearchParams();for(const [key,value] of Object.entries(values))if(value!==undefined&&value!=='')params.set(key,String(value));return params.size?'?'+params:''}
export function mergeMessages(old:Message[],incoming:Message[]){const rows=new Map(old.map(row=>[row.seq,row]));for(const row of incoming)rows.set(row.seq,row);return [...rows.values()].sort((a,b)=>a.seq-b.seq)}
export function visibleSequence(rows:Pick<Message,'seq'>[]){let through=0;for(const row of rows){if(row.seq===through+1)through=row.seq;else if(row.seq>through+1)break}return through}
