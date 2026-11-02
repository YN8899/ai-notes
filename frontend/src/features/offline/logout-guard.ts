import {database} from './database.ts'
import {OfflineRepository} from './repository.ts'
import {vaultSnapshot} from './vault.ts'
import {unfinished} from './types.ts'
export function logoutWarning(count:number|null){return '退出登录会永久清除本设备该账号的整个加密离线资料库、原件及同步队列。'+(count===null?'资料库已锁定或无法读取，未同步修改数量未知，可能有仅存于本机的内容。':`当前有 ${count} 项未同步操作。`)+ '未同步内容无法从服务器恢复。点击“取消”保留登录和本机资料，可先打开离线空间同步或下载原件；点击“确定”清除资料并退出。'}
export async function confirmOfflineLogout(owner:string,confirm:(message:string)=>boolean=window.confirm.bind(window)){
  let count:number|null=null
  const cap=vaultSnapshot().cap
  try{
    if(cap?.owner===owner)count=unfinished(await new OfflineRepository(cap).value()).length
    else if(!await database.header(owner))return true
  }catch{/* Fail closed to an explicit unknown-count warning. */}
  return confirm(logoutWarning(count))
}
