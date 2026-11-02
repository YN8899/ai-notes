import type {Json,Operation,Workspace} from './types.ts'
// JSONB may reorder object keys; value equality must not depend on serialization order.
export function equal(a:unknown,b:unknown):boolean{
  if(a===b)return true
  if(a===null||b===null||typeof a!=='object'||typeof b!=='object')return false
  if(Array.isArray(a)||Array.isArray(b)){
    if(!Array.isArray(a)||!Array.isArray(b)||a.length!==b.length)return false
    for(let i=0;i<a.length;i++)if(Object.hasOwn(a,i)!==Object.hasOwn(b,i)||!equal(a[i],b[i]))return false
    return true
  }
  const left=a as Json,right=b as Json,keys=Object.keys(left)
  return keys.length===Object.keys(right).length&&keys.every(key=>Object.hasOwn(right,key)&&equal(left[key],right[key]))
}
export function threeWay(base:Json,local:Json,server:Json,fields=Object.keys(local)){
  const merged:Json={...server},conflicts:string[]=[]
  const groups=fields.includes('body_json')||fields.includes('plain_text')?[...fields.filter(x=>!['body_json','plain_text'].includes(x)),'body']:fields
  for(const field of new Set(groups)){
    const keys=field==='body'?['body_json','plain_text']:[field]
    const pick=(value:Json)=>keys.map(k=>value[k])
    const b=pick(base),l=pick(local),s=pick(server)
    if(equal(l,b))continue
    if(equal(s,b)||equal(l,s)){for(const key of keys)merged[key]=local[key]}
    else conflicts.push(field)
  }
  return {merged,conflicts}
}
export function unchangedResource(base:Json,server:Json){
  const fields=['id','title','kind','notebook_id','tag_ids','body_json','plain_text','deleted_at','name','parent_id','folder_id','page_ids'].filter(k=>k in base)
  return fields.length>0&&fields.every(field=>field==='deleted_at'?!!base[field]===!!server[field]:equal(base[field],server[field]))
}
const scalarIds=['note_id','parent_id','folder_id','notebook_id']
const arrayIds=['tag_ids','page_ids']
export function mapPayload(payload:Json,mapping:Record<string,string>):Json{
  const out={...payload}
  for(const key of scalarIds)if(typeof out[key]==='string')out[key]=mapping[out[key] as string]||out[key]
  for(const key of arrayIds)if(Array.isArray(out[key]))out[key]=(out[key] as string[]).map(id=>mapping[id]||id)
  return out
}
export function mapOperation(op:Operation,mapping:Record<string,string>):Operation{return {...op,resource_id:mapping[op.resource_id]||op.resource_id,payload:mapPayload(op.payload,mapping)}}
export function applyMappings(w:Workspace,mapping:Record<string,string>){
  const id=(value:string)=>mapping[value]||value
  for(const list of [w.folders,w.notebooks,w.tags,w.notes])for(const row of list){Object.assign(row,mapPayload(row,mapping));row.id=id(row.id)}
  for(const page of w.pages){page.id=id(page.id);page.note_id=id(page.note_id);page.file_id=id(page.file_id)}
  for(const file of w.files){const old=file.id;file.id=id(old);for(const kind of ['original','reading','thumbnail']){const key=`${old}:${kind}`;if(w.assets[key]){w.assets[`${file.id}:${kind}`]=w.assets[key];if(file.id!==old)delete w.assets[key]}}}
  for(const pdf of w.pdf_imports){pdf.id=id(pdf.id);pdf.note_id=id(pdf.note_id);pdf.source_file_id=id(pdf.source_file_id)}
  for(const q of w.queue)if(!q.sent){q.operation=mapOperation(q.operation,mapping);q.base=mapPayload(q.base,mapping);if(typeof q.base.id==='string')q.base.id=id(q.base.id);if(q.noteId)q.noteId=id(q.noteId)}
}
