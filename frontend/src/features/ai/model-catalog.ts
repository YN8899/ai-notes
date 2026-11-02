export type ModelChoice={provider:string;model:string}
export type CatalogModel={id:string;name:string;supports_text:boolean;supports_vision:boolean;supports_tools:boolean;supports_streaming:boolean;supports_embedding:boolean}
export type Catalog={credential_source:'shared';platform_enabled:boolean;providers:{id:string;name:string;enabled:boolean;models:CatalogModel[]}[]}
export type ModelNeeds={vision?:boolean;tools?:boolean}
export const defaultModel:ModelChoice={provider:'deepseek',model:'deepseek-flash'}
export function modelKey(choice:ModelChoice){return JSON.stringify([choice.provider,choice.model])}
export function modelReady(catalog:Catalog|undefined,choice:ModelChoice,needs:ModelNeeds={}){
  if(!catalog?.platform_enabled||needs.vision&&needs.tools)return false
  const provider=catalog.providers.find(p=>p.id===choice.provider),model=provider?.models.find(m=>m.id===choice.model)
  return !!(provider?.enabled&&model?.supports_text&&model.supports_streaming&&(!needs.vision||model.supports_vision)&&(!needs.tools||model.supports_tools))
}
/** History is self-contained: aliases apply to the builtin provider only. */
export function modelLabel(provider:string|undefined,model:string){
  const id=provider||'deepseek'
  const old:Record<string,string>={'deepseek-flash':'DeepSeek Flash','deepseek-v4-pro':'DeepSeek V4 Pro','deepseek-chat':'DeepSeek Chat','deepseek-reasoner':'DeepSeek Reasoner'}
  return id+' / '+(id==='deepseek'?(old[model]||model):model)
}
