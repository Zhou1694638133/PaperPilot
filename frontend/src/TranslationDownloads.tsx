import { useState } from 'react'
import { Icon } from './components'
import { statusLabel, terminalStatus } from './api'
import type { Job } from './types'

export function TranslationDownloads({ jobId, ready, status, chineseJob, modelReady, busy, typesetAvailable = true, variant, onRestoreSource, onTypeset, onJobAction }: {
  jobId?: string; ready: boolean; status: string; variant: 'chinese' | 'bilingual'
  chineseJob?: Job; modelReady: boolean; busy: boolean; typesetAvailable?: boolean
  onRestoreSource?: () => void
  onTypeset: () => void; onJobAction: (job: Job, action: 'cancel' | 'retry') => void
}) {
  const [requested, setRequested] = useState('')
  const pdfUrl = jobId ? `/api/reports/pdf?${new URLSearchParams({job_id: jobId})}` : undefined
  const markdownUrl = jobId ? `/api/jobs/${encodeURIComponent(jobId)}/artifact` : undefined

  const chinese = variant === 'chinese'
  return <section className="translation-downloads" aria-label={chinese ? '中文译文下载' : '双语译文下载'}>
    <div className="translation-download-heading"><span className="translation-download-icon"><Icon name={ready ? 'check' : 'download'} size={22}/></span><div><h4>{chinese ? '中文译文下载' : '双语对照下载'}</h4><p>{status}</p></div></div>
    <div className="translation-download-actions">
      {chinese && !typesetAvailable && onRestoreSource && <button className="button primary" disabled={busy} onClick={onRestoreSource}><Icon name="book"/>恢复原图并启用图文版</button>}
      {chinese && typesetAvailable && (ready && chineseJob?.status === 'succeeded' ? <a className="button primary" href={`/api/jobs/${chineseJob.id}/chinese.pdf`} download onClick={() => setRequested('中文版 PDF')}><Icon name="download"/>下载中文版 PDF</a>
        : <button className="button primary" disabled={!ready || !modelReady || busy || !!chineseJob && !terminalStatus(chineseJob.status)} onClick={onTypeset}><Icon name="book"/>{chineseJob && !terminalStatus(chineseJob.status) ? '中文版排版中…' : '生成中文版 PDF'}</button>)}
      {chinese && !typesetAvailable && (ready && jobId ? <a className="button primary" href={`/api/jobs/${encodeURIComponent(jobId)}/chinese-text.pdf`} download onClick={() => setRequested('中文版 PDF（文字版）')}><Icon name="download"/>下载中文版 PDF（文字版）</a> : <button className="button primary" disabled><Icon name="download"/>下载中文版 PDF（文字版）</button>)}
      {!chinese && (ready && jobId ? <>
        <a className="button" href={pdfUrl} download onClick={() => setRequested('双语 PDF')}><Icon name="download"/>下载双语 PDF</a>
        <a className="button" href={markdownUrl} download onClick={() => setRequested('Markdown')}><Icon name="download"/>下载双语 Markdown</a>
      </> : <>
        <button className="button primary" disabled><Icon name="download"/>下载双语 PDF</button>
        <button className="button" disabled><Icon name="download"/>下载双语 Markdown</button>
      </>)}
    </div>
    {chinese && (typesetAvailable ? <p className="fineprint">中文版：黑色宋体、原图保留，公式和算法正常排版，仅含论文内容。首次生成仅补译标题、图注、表格和算法文字，正文复用已保存检查点；下载已有文件不调用模型。</p> : <p className="fineprint">图文版请先恢复原图与源文件，再翻译并生成 PDF。下方文字版仅为备用，复用已有译文，不重复调用模型。仅含已解析文本，无法保留原图与原版式；图表、公式和算法请同时核对上方原 PDF。双语版可在“双语对照”标签下载。</p>)}
    {chinese && typesetAvailable && !modelReady && chineseJob?.status !== 'succeeded' && <p className="notice">模型未配置：中文版的图表文字补译暂不可用，已有双语文件仍可下载。</p>}
    {chinese && chineseJob && chineseJob.status !== 'succeeded' && <div role="status"><p>{statusLabel(chineseJob.status)} · {chineseJob.stage} · {chineseJob.progress}%</p>{chineseJob.error && <p className="error-text">{chineseJob.error}</p>}{!terminalStatus(chineseJob.status) ? <button className="button small" onClick={() => onJobAction(chineseJob, 'cancel')}>取消排版</button> : ['failed', 'cancelled', 'needs_confirmation'].includes(chineseJob.status) && <button className="button small" disabled={busy || chineseJob.can_retry === false} onClick={() => onJobAction(chineseJob, 'retry')}>从排版检查点继续</button>}</div>}
    {requested && <p className="download-message" role="status">已请求{requested}，请在浏览器或 IDM 中查看下载进度和保存位置。PDF 排版可能需要片刻；若未完成，可在下载管理器中重试。</p>}
  </section>
}
