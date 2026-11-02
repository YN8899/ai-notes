import {sha256} from './crypto.ts'
import {onlineContext} from './network.ts'
import {OfflineRepository} from './repository.ts'
import {assetId,unfinished,type Snapshot} from './types.ts'
export const MIN_BUDGET=20*1024*1024,MAX_BUDGET=500*1024*1024
export function checkBudget(snapshot:Snapshot,budget:number,remaining?:number){
  if(!Number.isSafeInteger(budget)||budget<MIN_BUDGET||budget>MAX_BUDGET)throw new Error('缓存预算须为 20–500 MiB')
  const required=snapshot.metadata_bytes+snapshot.asset_bytes+snapshot.assets.length*64+Math.ceil(snapshot.metadata_bytes*.35)
  if(!Number.isSafeInteger(required)||required>budget)throw new Error('完整资料超过缓存预算；请提高预算或选择较少笔记。没有替换旧缓存')
  if(remaining!==undefined&&required>remaining*.85)throw new Error('浏览器可用空间不足以安全暂存完整新快照，旧缓存保持不变')
  return required
}
export async function cacheSnapshot(repo:OfflineRepository,scope:'all'|'selected',noteIds:string[],budget:number,progress:(message:string)=>void){
  const net=await onlineContext(repo.cap)
  try{
    const old=await repo.value();if(unfinished(old).length)throw new Error('存在未同步本机修改，请先同步或处理冲突，再替换离线快照')
    progress('正在生成一致的本人资料快照…')
    const snapshot=await net.json<Snapshot>('/offline/snapshot','POST',{scope,note_ids:scope==='all'?[]:noteIds});net.check()
    if(snapshot.owner_id!==repo.cap.owner||snapshot.version!==1||!/^[0-9a-f-]{36}$/i.test(snapshot.sync_epoch))throw new Error('服务器快照身份或版本无效')
    if(old.syncEpoch&&old.syncEpoch!==snapshot.sync_epoch)throw new Error('服务器空间已恢复或重建。请先核对、导出并人工迁移旧离线内容，不自动替换同步代次')
    const estimate=await navigator.storage?.estimate?.();net.check()
    const required=checkBudget(snapshot,budget,estimate?.quota===undefined?undefined:Math.max(0,estimate.quota-(estimate.usage||0)))
    const missing=snapshot.assets.filter(a=>!a.available).map(a=>`${a.file_id}/${a.kind}`)
    if(missing.length)throw new Error(`快照有 ${missing.length} 项文件尚未生成或不可用：${missing.slice(0,5).join('、')}。旧缓存保持不变`)
    const assets:Record<string,string>={};let done=0,bytes=0
    for(const asset of snapshot.assets){
      net.check();const r=await net.response(`/offline/files/${asset.file_id}/content?kind=${asset.kind}`)
      if(!r.ok)throw new Error(`缓存文件读取失败（${r.status}），旧缓存保持不变`)
      const data=await r.arrayBuffer();net.check();const actual=await sha256(data);net.check()
      const header=r.headers.get('X-Content-SHA256')?.toLowerCase()
      if(!header||actual!==header||(asset.sha256&&actual!==asset.sha256.toLowerCase())||(asset.bytes!==null&&data.byteLength!==asset.bytes))throw new Error('文件字节或 SHA256 校验不一致，未替换旧缓存')
      // Every original has a manifest hash; variants are independently header-verified.
      if(asset.kind==='original'&&!asset.sha256)throw new Error('原件缺少快照摘要，拒绝声称完整缓存')
      bytes+=data.byteLength
      if(bytes+snapshot.metadata_bytes>budget)throw new Error('实际文件超过缓存预算，未替换旧缓存')
      const key=`${snapshot.snapshot_id}:${asset.file_id}:${asset.kind}`
      await repo.stageAsset(key,new Blob([data],{type:asset.mime}));net.check();assets[assetId(asset.file_id,asset.kind)]=key
      progress(`正在验证并加密缓存 ${++done}/${snapshot.assets.length} · ${(bytes/1024/1024).toFixed(1)} MiB`)
    }
    net.check();await repo.mutate(w=>{
      if(unfinished(w).length)throw new Error('缓存期间产生了本机修改，停止发布新快照；原投影完整保留')
      if(w.syncEpoch&&w.syncEpoch!==snapshot.sync_epoch)throw new Error('同步代次改变，拒绝发布快照')
      Object.assign(w,{syncEpoch:snapshot.sync_epoch,folders:snapshot.folders,notebooks:snapshot.notebooks,tags:snapshot.tags,notes:snapshot.notes,pages:snapshot.pages,files:snapshot.files,pdf_imports:snapshot.pdf_imports,assets,cache:{snapshotId:snapshot.snapshot_id,contentVersion:snapshot.content_version,bytes:Math.max(bytes+snapshot.metadata_bytes,required),cachedAt:new Date().toISOString(),scope,missing:[]}})
    });net.check();progress(`完整缓存已发布：${snapshot.notes.length} 本笔记，${snapshot.assets.length} 项文件`)
  }finally{net.close()}
}
