import { useMutation, useQuery } from '@tanstack/react-query'
import { api, type AiCatalog, type AiConnection } from '../../api'
import { NoteError } from '../notes/NoteError'
import './ai.css'

const testMessages: Record<string, string> = {
  connected: '连接成功，可用模型已确认。',
  credential_rejected: '共享服务凭据被拒绝，请管理员检查本机配置。',
  provider_rate_limited: '服务商当前限流，请稍后手动重试。',
  provider_unavailable: '服务商暂不可用，请稍后重试。',
  provider_unreachable: '无法连接服务商，请稍后重试。',
  provider_invalid_response: '服务商返回了无法识别的模型列表。',
  models_unavailable: '共享账户暂无本应用支持的模型。',
}

export default function AiSettings({ owner, admin }: { owner: string; admin: boolean }) {
  const catalog = useQuery({ queryKey: ['ai', owner, 'providers'], queryFn: () => api<AiCatalog>('/ai/providers') })
  const test = useMutation({
    mutationFn: () => api<AiConnection>('/ai/connection-test', 'POST', {}),
    retry: false,
  })

  return <>
    <section className="panel">
      <div className="panel-heading"><h3>共享模型服务</h3><span className="badge">成员共用</span></div>
      <p className="muted">管理员统一提供 DeepSeek 服务，所有登录成员共用，无需填写个人 API Key。</p>
      <p className="muted">凭据由服务端保管，网页不会显示。AI 对话将在后续开放。</p>
      <NoteError error={catalog.error || test.error}/>
      {catalog.isPending ? <p role="status">正在读取模型设置…</p> : null}
      {catalog.data ? <p role="status" className={catalog.data.platform_enabled ? 'muted' : 'error'}>
        {catalog.data.platform_enabled ? '共享模型服务已配置。' : '共享模型服务尚未配置，请联系管理员。'}
      </p> : null}
      {admin ? <>
        <div className="ai-credential-actions"><button className="secondary"
          disabled={!catalog.data?.platform_enabled || test.isPending} onClick={() => test.mutate()}>
          {test.isPending ? '正在测试…' : '测试连接'}
        </button></div>
        <p className="muted">连接测试仅读取服务商的模型列表，不发送笔记，也不生成回答。</p>
      </> : null}
      {test.data ? <p role="status" className={test.data.status === 'valid' ? 'muted' : 'error'}>
        {testMessages[test.data.code] || '暂时无法确认连接状态，请重新测试。'}
      </p> : null}
    </section>
    {catalog.data?.providers.map(provider => <section className="panel" key={provider.id}>
      <h3>{provider.name}</h3>
      <div className="table-wrap"><table>
        <caption className="ai-model-caption">模型能力</caption>
        <thead><tr><th>模型</th><th>文字</th><th>图片</th><th>流式</th>{admin ? <th>本次连接验证</th> : null}</tr></thead>
        <tbody>{provider.models.map(model => <tr key={model.id}>
          <td><strong>{model.name}</strong><small>{model.id}</small></td>
          <td>{model.supports_text ? '支持' : '不支持'}</td>
          <td>{model.supports_vision ? '支持' : '不支持'}</td>
          <td>{model.supports_streaming ? '支持' : '不支持'}</td>
          {admin ? <td>{test.data?.available_models.includes(model.id) ? '已确认' : '未确认'}</td> : null}
        </tr>)}</tbody>
      </table></div>
    </section>)}
  </>
}
