export type SearchFilters={q:string;kind:string;folder_id:string;notebook_id:string;tag_id:string}

// API offsets count Unicode code points, rather than JavaScript UTF-16 units.
export function highlightedParts(text:string,start:number,end:number):[string,string,string] {
  const points=Array.from(text)
  if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<=start||end>points.length)return [text,'','']
  return [points.slice(0,start).join(''),points.slice(start,end).join(''),points.slice(end).join('')]
}

export function searchParams(filters:SearchFilters,cursor=''):URLSearchParams {
  const params=new URLSearchParams({q:filters.q.trim(),limit:'20'})
  for(const key of ['kind','folder_id','notebook_id','tag_id'] as const)if(filters[key])params.set(key,filters[key])
  if(cursor)params.set('cursor',cursor)
  return params
}

export function searchScope(owner:string,sessionId:string,filters:SearchFilters):string {
  return JSON.stringify([owner,sessionId,searchParams(filters).toString()])
}

export function nextSearchPage(cursors:string[],next:string|null|undefined):string[] {
  return next&&!cursors.includes(next)?[...cursors,next]:cursors
}
