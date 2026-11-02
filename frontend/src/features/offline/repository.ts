import {database,ConcurrentWriteError,type OfflineDatabase,type PrivateRow} from './database.ts'
import {decode,encode,open,seal} from './crypto.ts'
import type {VaultCapability,Workspace} from './types.ts'
export class OfflineRepository{
  readonly cap:VaultCapability
  readonly db:OfflineDatabase
  constructor(cap:VaultCapability,db:OfflineDatabase=database){this.cap=cap;this.db=db}
  async load(){
    this.cap.check();const row=await this.db.read(this.cap,'workspace')
    if(!row)throw new Error('离线资料已被清除，请重新缓存')
    const value=decode<Workspace>(await open(this.cap.key,this.cap.owner,'records','workspace',row));this.cap.check()
    return {value,version:row.version}
  }
  async value(){return (await this.load()).value}
  async mutate<T>(change:(value:Workspace)=>T,blobs:{key:string;blob:Blob}[]=[]):Promise<T>{
    const encrypted:PrivateRow[]=[]
    for(const {key,blob} of blobs){this.cap.check();const bytes=await blob.arrayBuffer();this.cap.check();const envelope=await seal(this.cap.key,this.cap.owner,'assets',key,bytes);this.cap.check();encrypted.push({...envelope,id:`${this.cap.owner}:asset:${key}`,owner:this.cap.owner,version:1})}
    for(let retry=0;retry<4;retry++){
      this.cap.check();const {value,version}=await this.load();this.cap.check();const result=change(value)
      const envelope=await seal(this.cap.key,this.cap.owner,'records','workspace',encode(value));this.cap.check()
      try{await this.db.put(this.cap,version,[...encrypted,{...envelope,id:`${this.cap.owner}:workspace`,owner:this.cap.owner,version:version+1}]);this.cap.check();return result}catch(error){if(error instanceof ConcurrentWriteError&&retry<3)continue;throw error}
    }
    throw new ConcurrentWriteError()
  }
  async stageAsset(key:string,blob:Blob){
    // Staging is intentionally unpublished until the final atomic manifest switch.
    await this.mutate(()=>{},[{key,blob}])
  }
  async blob(key:string,mime:string){
    this.cap.check();const row=await this.db.read(this.cap,`asset:${key}`)
    if(!row)throw new Error('文件尚未缓存或已被浏览器清除，请联网重新缓存')
    const bytes=await open(this.cap.key,this.cap.owner,'assets',key,row);this.cap.check();return new Blob([bytes],{type:mime})
  }
}
