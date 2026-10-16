import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, date, type AiCatalog, type AiCredential, type AiProvider } from '../../api'
import { NoteError } from '../notes/NoteError'
import './ai.css'

const testMessages: Record<string, string> = {
  connected: '连接成功，可用模型已确认。',
  credential_rejected: '凭据被拒绝，请检查后替换 Key。',
  provider_rate_limited: '服务商当前限流，请稍后手动重试。',
  provider_unavailable: '服务商暂不可用，请稍后重试。',
  provider_unreachable: '无法连接服务商，请稍后重试。',
  provider_invalid_response: '服务商返回了无法识别的模型列表。',
  models_unavailable: '当前账户暂无本应用支持的模型。',
}

type ProviderProps = {
  provider: AiProvider
  credential?: AiCredential
  storageReady: boolean
  onChanged: () => Promise<unknown>
}

function ProviderSettings({ provider, credential, storageReady, onChanged }: ProviderProps) {
  const [apiKey, setApiKey] = useState('')
  const save = useMutation({
    mutationFn: () => api<AiCredential>('/ai/credentials/' + provider.id, 'PUT', {
      api_key: apiKey, revision: credential?.revision ?? null,
    }),
    onSuccess: async () => { setApiKey(''); await onChanged() },
    onError: onChanged,
  })
  const test = useMutation({
    mutationFn: () => api<AiCredential>('/ai/credentials/' + credential!.id + '/test', 'POST', {
      revision: credential!.revision,
    }),
    onSettled: onChanged,
  })
  const remove = useMutation({
    mutationFn: () => api('/ai/credentials/' + credential!.id, 'DELETE', { revision: credential!.revision }),
    onSuccess: async () => { setApiKey(''); await onChanged() },
    onError: onChanged,
  })
  const busy = save.isPending || test.isPending || remove.isPending
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    test.reset()
    remove.reset()
    save.mutate()
  }
  function deleteKey() {
    if (window.confirm('删除这个个人 Key？再次使用时需要重新填写。')) {
      save.reset()
      test.reset()
      remove.mutate()
    }
  }

  return <section className="panel">
    <div className="panel-heading"><h3>{provider.name}</h3><span className="badge">个人凭据</span></div>
    <p className="muted">Key 仅用于你的账户，保存后只显示掩码。更换 Key 会清除旧的连接测试结果。</p>
    {credential ? <div className="secret-box">
      <span>当前 Key</span><code aria-label="已保存的 Key 掩码">{credential.masked_key}</code>
      <small className="muted">更新于 {date(credential.updated_at)}</small>
    </div> : <p className="muted">尚未保存 {provider.name} Key。</p>}
    <form onSubmit={submit}>
      <label>{credential ? '新的 DeepSeek API Key' : 'DeepSeek API Key'}
        <input type="password" value={apiKey} onChange={event => setApiKey(event.target.value)}
          required minLength={16} maxLength={512} autoComplete="off" spellCheck={false}
          disabled={!storageReady || busy} placeholder="输入你的个人 Key"/>
      </label>
      <div className="ai-credential-actions">
        <button className="primary" disabled={!storageReady || busy || !apiKey}>
          {save.isPending ? '正在保存…' : credential ? '替换 Key' : '保存 Key'}
        </button>
        {credential ? <>
          <button type="button" className="secondary" disabled={!storageReady || busy}
            onClick={() => { save.reset(); remove.reset(); test.mutate() }}>
            {test.isPending ? '正在测试…' : '测试连接'}
          </button>
          <button type="button" className="text-button" disabled={busy} onClick={deleteKey}>删除 Key</button>
        </> : null}
      </div>
    </form>
    <NoteError error={save.error || test.error || remove.error}/>
    {credential?.test_code ? <p role="status" className={credential.test_status === 'valid' ? 'muted' : 'error'}>
      {testMessages[credential.test_code] || '暂时无法确认连接状态，请重新测试。'}
      {credential.last_tested_at ? <small> {date(credential.last_tested_at)}</small> : null}
    </p> : null}
    <p className="muted">连接测试仅读取服务商的模型列表，不发送笔记，也不生成回答。保存 Key 后需要手动测试。</p>
    <div className="table-wrap"><table>
      <caption className="ai-model-caption">模型能力</caption>
      <thead><tr><th>模型</th><th>文字</th><th>图片</th><th>流式</th><th>连接验证</th></tr></thead>
      <tbody>{provider.models.map(model => <tr key={model.id}>
        <td><strong>{model.name}</strong><small>{model.id}</small></td>
        <td>{model.supports_text ? '支持' : '不支持'}</td>
        <td>{model.supports_vision ? '支持' : '不支持'}</td>
        <td>{model.supports_streaming ? '支持' : '不支持'}</td>
        <td>{credential?.available_models.includes(model.id) ? '已确认' : '未确认'}</td>
      </tr>)}</tbody>
    </table></div>
  </section>
}

export default function AiSettings({ owner }: { owner: string }) {
  const client = useQueryClient()
  const queryKey = ['ai', owner, 'credentials']
  const catalog = useQuery({ queryKey: ['ai', owner, 'providers'], queryFn: () => api<AiCatalog>('/ai/providers') })
  const credentials = useQuery({ queryKey, queryFn: () => api<AiCredential[]>('/ai/credentials') })
  const onChanged = () => client.invalidateQueries({ queryKey })

  return <>
    <section className="panel">
      <h3>个人模型设置</h3>
      <p className="muted">先保存个人 Key 并测试连接。AI 对话将在后续开放，平台公共 Key 暂未开放。</p>
      <NoteError error={catalog.error || credentials.error}/>
      {catalog.isPending || credentials.isPending ? <p role="status">正在读取模型设置…</p> : null}
      {catalog.data && !catalog.data.storage_ready ? <p role="status" className="error">管理员尚未配置模型凭据存储，暂时无法保存或测试 Key。</p> : null}
    </section>
    {catalog.data && credentials.data ? catalog.data.providers.map(provider => <ProviderSettings
      key={provider.id} provider={provider} credential={credentials.data.find(item => item.provider === provider.id)}
      storageReady={catalog.data.storage_ready} onChanged={onChanged}/>) : null}
  </>
}
