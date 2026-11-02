export type FeedNotice={id:string;seq:number;message:string;read_at:string|null;created_at:string;target:'reviews'|null}
export type FeedPage={items:FeedNotice[];next_after_seq:number;has_more:boolean;head_seq:number;read_revision:number}
export type FeedState={items:FeedNotice[];after:number;head:number;revision:number;pending:boolean}
export const emptyFeed=():FeedState=>({items:[],after:0,head:0,revision:0,pending:false})

export function advanceFeed(state:FeedState,page:FeedPage):FeedState{
  if(!Number.isSafeInteger(page.head_seq)||page.head_seq<0||!Number.isSafeInteger(page.next_after_seq)||page.next_after_seq<state.after||page.next_after_seq>page.head_seq)throw new Error('通知补拉位置无效，请重新读取。')
  let previous=state.after
  const notices=new Map(state.items.map(row=>[row.id,row]))
  for(const row of page.items){
    if(!Number.isSafeInteger(row.seq)||row.seq!==previous+1||row.seq>page.next_after_seq)throw new Error('通知补拉存在缺口，请重新读取。')
    previous=row.seq;notices.set(row.id,row)
  }
  if(previous!==page.next_after_seq||page.has_more&&page.items.length===0)throw new Error('通知补拉未推进，请重试。')
  // UI shows the latest 100; cursor advances only AFTER every durable page has
  // been consumed, including pages beyond the old 100-row list endpoint.
  return {items:[...notices.values()].sort((a,b)=>b.seq-a.seq).slice(0,100),after:previous,head:page.head_seq,revision:page.read_revision,pending:page.has_more}
}

export function refreshReadWindow(state:FeedState,page:FeedPage):FeedState{
  if(!state.items.length)return state
  const lower=Math.min(...state.items.map(row=>row.seq))-1
  try{
    const checked=advanceFeed({...emptyFeed(),after:lower},page)
    const current=new Map(checked.items.map(row=>[row.seq,row]))
    if(checked.after<state.after||state.items.some(row=>current.get(row.seq)?.id!==row.id))throw new Error('window_changed')
    return {...state,items:state.items.map(row=>current.get(row.seq)!),head:page.head_seq,revision:page.read_revision,
      // Rows created during this refresh remain unacknowledged. The bounded
      // catch-up query consumes them from state.after on the next iteration.
      pending:state.pending||page.head_seq>state.after}
  }catch{
    // Missing/replaced rows cannot be declared fresh at the new revision.
    // Restart authoritative pagination rather than skipping to a later head.
    return {...emptyFeed(),pending:true}
  }
}
