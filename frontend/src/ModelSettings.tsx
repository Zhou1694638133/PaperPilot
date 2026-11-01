import { useEffect, useState } from 'react'
import { api, errorMessage, write } from './api'

type ModelConfig = {
  base_url: string; model: string; has_api_key: boolean; configured: boolean
  revision: string; source: 'backend' | 'environment'
}

export function ModelSettings({ onSaved }: { onSaved: () => Promise<void> }) {
  const [config, setConfig] = useState<ModelConfig | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [key, setKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const [busy, setBusy] = useState('loading')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  function apply(data: ModelConfig) {
    setConfig(data); setBaseUrl(data.base_url); setModel(data.model); setKey(''); setClearKey(false)
  }
  useEffect(() => {
    let active = true
    void api<ModelConfig>('/model-config').then(data => { if (active) apply(data) })
      .catch(cause => { if (active) setError(errorMessage(cause)) })
      .finally(() => { if (active) setBusy('') })
    return () => { active = false }
  }, [])

  async function reload() {
    setBusy('loading'); setError(''); setMessage('')
    try { apply(await api<ModelConfig>('/model-config')) }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy('') }
  }

  async function submit(action: 'save' | 'test') {
    if (!config) return
    setBusy(action); setMessage(''); setError('')
    const payload = { base_url: baseUrl, model, api_key: key || null, clear_key: clearKey, revision: config.revision }
    try {
      if (action === 'save') {
        apply(await api<ModelConfig>('/model-config', write('PUT', payload)))
        setMessage('配置已保存在后端，密钥输入框已清空。API 与 worker 自动读取，无需重启。')
        await onSaved()
      } else {
        const result = await api<{ok: boolean; message: string}>('/model-config/test', write('POST', payload))
        setMessage(result.message)
      }
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy('') }
  }

  return <div className="model-settings" onKeyDown={event => {
    if (event.key === 'Enter' && event.target instanceof HTMLInputElement && event.target.type !== 'checkbox') event.preventDefault()
  }}>
    <div className="section-heading"><h3>大模型配置</h3><span className={`status-pill ${config?.configured ? 'succeeded' : 'queued'}`}>{config?.configured ? '已配置' : '未配置'}</span></div>
    <p className="fineprint">用于摘要解读、全文精读和翻译。兼容 OpenAI Chat Completions 协议。{config ? config.source === 'environment' ? '当前使用 .env / 环境变量。' : '当前使用后端保存的配置。' : '等待读取后端配置。'}</p>
    <fieldset disabled={!!busy || !config}>
      <label className="field-label">API 地址<input type="url" value={baseUrl} onChange={event => { setBaseUrl(event.target.value); setMessage('') }} placeholder="https://api.example.com/v1" autoComplete="off" maxLength={1000}/><small>填写基地址，系统追加 /chat/completions；远程服务使用 HTTPS。</small></label>
      <label className="field-label">模型名称<input value={model} onChange={event => { setModel(event.target.value); setMessage('') }} placeholder="填写服务商支持的完整模型名称" autoComplete="off" maxLength={200}/></label>
      <label className="field-label">API 密钥<input type="password" value={key} onChange={event => { setKey(event.target.value); setMessage('') }} placeholder={config?.has_api_key ? '后端已保存密钥；留空保持不变' : '填写 API 密钥'} autoComplete="new-password" spellCheck={false} maxLength={8192} disabled={clearKey}/><small>已保存密钥不会回传或回填。不写入浏览器存储；保存成功或离开此页后清空输入。更换地址需重新填写密钥。</small></label>
      <label className="model-clear-key"><input type="checkbox" checked={clearKey} onChange={event => { setClearKey(event.target.checked); setKey(''); setMessage('') }}/>保存时清除后端密钥，停用模型能力</label>
      <p className="notice">连接测试会将测试提示发送到上方 API 地址，并执行一次简短生成，可能产生少量费用。测试不会自动保存配置；成功仅表示本次请求兼容。</p>
      <div className="button-row"><button type="button" className="button primary" disabled={!baseUrl.trim() || !model.trim()} onClick={() => void submit('save')}>{busy === 'save' ? '保存中…' : '保存模型配置'}</button><button type="button" className="button" disabled={!baseUrl.trim() || !model.trim() || clearKey || (!key && !config?.has_api_key)} onClick={() => void submit('test')}>{busy === 'test' ? '连接测试中…' : '测试连接'}</button></div>
    </fieldset>
    <button type="button" className="text-button" disabled={!!busy} onClick={() => void reload()}>{busy === 'loading' ? '正在读取配置…' : '重新加载后端配置'}</button>
    {message && <p className="notice" role="status">{message}</p>}
    {error && <p className="notice model-config-error" role="alert">{error}</p>}
  </div>
}
