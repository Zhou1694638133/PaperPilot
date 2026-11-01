import { useEffect, useRef, useState } from 'react'
import { api, errorMessage } from './api'
import { Icon } from './components'
import type { Job, Mode, Paper } from './types'

export function LocalPaperUpload({ mode, modelReady, onImported, onSettings }: {
  mode: Mode; modelReady: boolean
  onImported: (paper: Paper, job: Job, reused: boolean) => void
  onSettings: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [mode])
  function selectFile(next: File | null) {
    setError(''); setFile(null)
    if (!next) return
    if (!next.name.toLowerCase().endsWith('.pdf')) { setError('目前仅支持 PDF 文件。'); return }
    if (!next.size || next.size > 20 * 1024 * 1024) { setError('请选择非空且不超过 20 MiB 的 PDF。'); return }
    setFile(next)
  }
  async function upload() {
    if (!file || busy || mode !== 'live') return
    const active = new AbortController()
    controller.current = active
    setBusy(true); setError('')
    try {
      const result = await api<{paper: Paper; job: Job; reused: boolean}>(`/local-papers?${new URLSearchParams({filename:file.name})}`, {
        method:'POST', headers:{'Content-Type':'application/pdf'}, body:file, signal:active.signal,
      })
      if (!active.signal.aborted) onImported(result.paper, result.job, result.reused)
    } catch (cause) {
      setError(active.signal.aborted ? '已停止等待上传。若文件已送达，可再次上传，系统会按内容去重并复用任务。' : errorMessage(cause))
    } finally { controller.current = null; setBusy(false) }
  }
  return <section className="local-upload" aria-label="本地论文上传与翻译">
    <div className="local-upload-heading"><Icon name="book" size={23}/><div><h3>上传本地论文 · 翻译与阅读</h3><p>上传 PDF → 核对解析内容 → 全文翻译 → 双语对照与下载</p></div><span className="tag neutral">PDF · ≤ 20 MiB</span></div>
    <div className="local-upload-actions">
      <label className="local-file-picker">选择本地 PDF<input type="file" accept=".pdf,application/pdf" aria-label="选择本地 PDF" disabled={busy || mode !== 'live'} onChange={event => selectFile(event.target.files?.[0] || null)}/></label>
      <button className="button primary" disabled={!file || busy || mode !== 'live'} onClick={() => void upload()}>{busy ? '正在上传并创建解析任务…' : '上传并解析'}<Icon name="arrow" size={15}/></button>
      {busy && <button className="button" onClick={() => controller.current?.abort()}>取消上传</button>}
    </div>
    {file && <p className="local-file-name">{file.name} · {(file.size / 1024 / 1024).toFixed(2)} MiB</p>}
    <p className="fineprint">原件保存在本机后端，自动加入文库；重复文件复用已有任务。翻译时，已解析文本会发送至你配置的模型服务。扫描件暂不支持 OCR，图片、公式和版式需对照原 PDF。</p>
    {mode !== 'live' ? <p className="notice">请切换为 Live 真实模式后上传本地论文。</p> : !modelReady && <p className="notice">模型未配置：可上传、解析和阅读。<button className="text-button" onClick={onSettings}>配置模型后翻译 →</button></p>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </section>
}
