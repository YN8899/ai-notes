type MarkerStorage=Pick<Storage,'getItem'|'setItem'|'removeItem'>
const prefix='ai-notes:home-default:v1:'
/** scope is the existing nonsecret tab-session identity, never an auth token. */
export function createStartupDefaults(storage:()=>MarkerStorage,sessionScope:()=>string){
  return {
    has(owner:string){try{const scope=sessionScope();return !!owner&&!!scope&&storage().getItem(prefix+owner)===scope}catch{return false}},
    mark(owner:string){try{const scope=sessionScope();if(owner&&scope)storage().setItem(prefix+owner,scope)}catch{/* In-memory navigation still works if storage is unavailable. */}},
    clear(owner:string){try{storage().removeItem(prefix+owner)}catch{/* Old markers cannot match the next login's fresh scope. */}},
  }
}
