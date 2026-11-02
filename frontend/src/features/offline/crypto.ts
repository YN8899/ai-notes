import type {Envelope,VaultHeader} from './types.ts'
export const ITERATIONS=600_000
const encoder=new TextEncoder(),decoder=new TextDecoder()
export function aad(owner:string,store:string,key:string){return encoder.encode(JSON.stringify([owner,store,key,1]))}
function validateHeader(header:VaultHeader){
  if(header.schema!==1||!/^[0-9a-f-]{36}$/i.test(header.owner)||header.iterations!==ITERATIONS||!(header.salt instanceof Uint8Array)||header.salt.length!==16||header.wrapped.iv.length!==12||header.wrapped.ciphertext.byteLength!==48)throw new Error('本机资料包装参数无效或不受支持')
}
async function wrappingKey(password:string,salt:Uint8Array,iterations:number){
  if(password.length<10||password.length>256)throw new Error('本机离线口令须为 10–256 个字符')
  if(iterations!==ITERATIONS)throw new Error('不受支持的口令计算参数')
  const material=await crypto.subtle.importKey('raw',encoder.encode(password),'PBKDF2',false,['deriveKey'])
  return crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt,iterations},material,{name:'AES-GCM',length:256},false,['encrypt','decrypt'])
}
export async function seal(key:CryptoKey,owner:string,store:string,id:string,bytes:BufferSource):Promise<Envelope>{
  const iv=crypto.getRandomValues(new Uint8Array(12))
  return {iv,ciphertext:await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:aad(owner,store,id)},key,bytes)}
}
export async function open(key:CryptoKey,owner:string,store:string,id:string,envelope:Envelope){
  if(envelope.iv.length!==12)throw new Error('密文参数无效')
  return crypto.subtle.decrypt({name:'AES-GCM',iv:envelope.iv,additionalData:aad(owner,store,id)},key,envelope.ciphertext)
}
export async function createHeader(owner:string,password:string){
  if(!/^[0-9a-f-]{36}$/i.test(owner))throw new Error('账号标识无效')
  const salt=crypto.getRandomValues(new Uint8Array(16)),raw=crypto.getRandomValues(new Uint8Array(32))
  try{
    const wrapper=await wrappingKey(password,salt,ITERATIONS)
    const wrapped=await seal(wrapper,owner,'header','data-key',raw)
    const key=await crypto.subtle.importKey('raw',raw,'AES-GCM',false,['encrypt','decrypt'])
    return {header:{owner,schema:1 as const,salt,iterations:ITERATIONS,wrapped},key}
  }finally{raw.fill(0)}
}
export async function unlockHeader(header:VaultHeader,password:string){
  validateHeader(header)
  const wrapper=await wrappingKey(password,header.salt,header.iterations)
  let raw:ArrayBuffer
  try{raw=await open(wrapper,header.owner,'header','data-key',header.wrapped)}catch{throw new Error('本机离线口令不正确，或密文已损坏')}
  try{return await crypto.subtle.importKey('raw',raw,'AES-GCM',false,['encrypt','decrypt'])}finally{new Uint8Array(raw).fill(0)}
}
export const encode=(value:unknown)=>encoder.encode(JSON.stringify(value))
export const decode=<T,>(bytes:ArrayBuffer):T=>JSON.parse(decoder.decode(bytes)) as T
export async function sha256(bytes:ArrayBuffer){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('')}
