import {modelKey,modelReady,type Catalog,type ModelChoice,type ModelNeeds} from './model-catalog'
export function ModelPicker({catalog,value,onChange,disabled,vision,tools,label='模型'}:{catalog?:Catalog;value:ModelChoice;onChange:(value:ModelChoice)=>void;disabled?:boolean;label?:string}&ModelNeeds){
  const options=catalog?.providers.flatMap(p=>p.models.map(m=>({choice:{provider:p.id,model:m.id},label:p.name+' / '+m.name+' · '+m.id})))||[]
  return <div className="ai-model-picker"><label>{label}<select aria-label={label} value={modelKey(value)} disabled={disabled} onChange={event=>{const option=options.find(o=>modelKey(o.choice)===event.target.value);if(option)onChange(option.choice)}}>
    {!options.some(o=>modelKey(o.choice)===modelKey(value))?<option value={modelKey(value)} disabled>请选择可用模型</option>:null}
    {options.map(o=><option key={modelKey(o.choice)} value={modelKey(o.choice)} disabled={!modelReady(catalog,o.choice,{vision,tools})}>{o.label}</option>)}
  </select></label>{vision&&tools?<p className="error" role="status">当前不支持图片 / PDF 与工具同时使用。请关闭工具或切换为文字资料。</p>:!modelReady(catalog,value,{vision,tools})?<p className="muted" role="status">请选择已启用、支持流式回复及本次{vision?'视觉':tools?'工具':'文字'}能力的模型。模型 ID 按服务端原值发送。</p>:null}</div>
}
