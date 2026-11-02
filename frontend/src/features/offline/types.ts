export type Json=Record<string,unknown>
export type Entity={id:string;revision:number;name:string;parent_id?:string|null;folder_id?:string|null}
export type Note={id:string;revision:number;title:string;kind:'text'|'image';notebook_id:string|null;tag_ids:string[];body_json:Json;plain_text:string;deleted_at:string|null}
export type Page={id:string;note_id:string;file_id:string;position:number;revision:number;pdf_page_number?:number|null;local?:boolean}
export type Asset={file_id:string;kind:'original'|'reading'|'thumbnail';mime:string;bytes:number|null;sha256:string|null;available:boolean}
export type FileInfo={id:string;filename:string;mime:string;bytes:number|null;status:string}
export type PdfInfo={id:string;note_id:string;source_file_id:string;status:string;page_count:number|null;completed_pages:number;error_code?:string|null}
export type Snapshot={version:1;sync_epoch:string;snapshot_id:string;content_version:string;owner_id:string;created_at:string;scope:'all'|'selected';folders:Entity[];notebooks:Entity[];tags:Entity[];notes:Note[];pages:Page[];pdf_imports:PdfInfo[];files:FileInfo[];assets:Asset[];total_rows:number;metadata_bytes:number;asset_bytes:number}
export const kinds=['folder.create','folder.update','folder.delete','notebook.create','notebook.update','notebook.delete','tag.create','tag.update','tag.delete','note.create','note.update','note.trash','note.restore','page.order','page.remove','upload.create','upload.complete','pdf.create','pdf.start'] as const
export type Kind=typeof kinds[number]
export type Operation={operation_id:string;sync_epoch:string;kind:Kind;resource_id:string;base_revision:number|null;payload:Json}
export type Current={resource_kind:string;exists:boolean;folder?:Entity|null;notebook?:Entity|null;tag?:Entity|null;note?:Note|null;pages?:Page[];file?:FileInfo|null;status?:string|null}
export type Conflict={base:Json;local:Json;server:Current|null;fields:string[];message:string}
export type QueueItem={operation:Operation;depends:string[];base:Json;state:'pending'|'sending'|'conflict'|'done';sent:boolean;error?:string;conflict?:Conflict;assetKey?:string;transport?:Transport;continuation?:string;noteId?:string;localPageId?:string;localFileId?:string}
export type Transport={upload_id?:string|null;import_id?:string|null;file_id?:string|null;note_id?:string|null;page_id?:string|null}
export type SyncResult={operation_id:string;kind:string;resource_id:string;resource_mapping:Record<string,string>;applied_revision:number|null;transport:Transport}
export type Workspace={schema:1;syncEpoch:string|null;syncBlocked?:string;folders:Entity[];notebooks:Entity[];tags:Entity[];notes:Note[];pages:Page[];files:FileInfo[];pdf_imports:PdfInfo[];assets:Record<string,string>;queue:QueueItem[];cache:{snapshotId:string;contentVersion:string;bytes:number;cachedAt:string;scope:string;missing:string[]}|null;history:{note_id:string;value:Json;at:string}[]}
export const emptyWorkspace=():Workspace=>({schema:1,syncEpoch:null,folders:[],notebooks:[],tags:[],notes:[],pages:[],files:[],pdf_imports:[],assets:{},queue:[],cache:null,history:[]})
export type Envelope={iv:Uint8Array;ciphertext:ArrayBuffer}
export type VaultHeader={owner:string;schema:1;salt:Uint8Array;iterations:number;wrapped:Envelope}
export type VaultCapability={readonly owner:string;readonly generation:number;readonly key:CryptoKey;readonly signal:AbortSignal;check:()=>void}
export const uuid=()=>crypto.randomUUID()
export const assetId=(id:string,kind='original')=>`${id}:${kind}`
export const unfinished=(w:Workspace)=>w.queue.filter(x=>x.state!=='done')
