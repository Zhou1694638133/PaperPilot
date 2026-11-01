import { useEffect, useState } from 'react'
import { api, errorMessage, statusLabel, terminalStatus, write } from './api'
import { EmptyState, Icon, JobCard } from './components'
import { LocalPaperUpload } from './LocalPaperUpload'
import type { Job, Mode, Paper } from './types'

export function LocalPapersPage({ mode, modelReady, selected, jobs, onOpen, onImported, onSettings }: {
  mode: Mode; modelReady: boolean; selected: Paper | null; jobs: Job[]
  onOpen: (paper: Paper) => void; onImported: (paper: Paper, job: Job, reused: boolean) => void; onSettings: () => void
}) {
  const [papers, setPapers] = useState<Paper[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(''); setPapers([])
    if (mode !== 'live') { setLoading(false); return }
    void api<{papers: Paper[]}>('/local-papers', {signal: controller.signal}).then(data => setPapers(data.papers))
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [mode, refresh])
  return <>
    <LocalPaperUpload mode={mode} modelReady={modelReady} onSettings={onSettings} onImported={(paper, job, reused) => {
      setRefresh(value => value + 1); onImported(paper, job, reused)
    }}/>
    <section className="local-history" aria-label="本地论文历史">
      <div className="section-heading"><h2>已上传的论文</h2><span>{papers.length} 篇</span><button className="button small" onClick={() => setRefresh(value => value + 1)} disabled={loading}><Icon name="refresh" size={15}/>刷新列表</button></div>
      {loading ? <p role="status">正在读取本地论文…</p> : error ? <p className="error-text" role="alert">{error} · 点击刷新列表重试。</p> : !papers.length ? <EmptyState title={mode === 'live' ? '上传第一篇本地论文' : '请切换真实模式'} icon="book"><p>上传后可核对摘要、精读全文、翻译、双语阅读和下载。原件与任务记录保存在本机。</p></EmptyState> : <div className="local-paper-list">{papers.map(paper => {
        const task = jobs.find(job => job.paper_id === paper.id && job.mode === 'live')
        return <button className={`local-paper-item ${selected?.id === paper.id ? 'active' : ''}`} key={paper.id} onClick={() => onOpen(paper)}><Icon name="book"/><span><strong>{paper.title}</strong><small>{paper.uploaded_at ? `上传于 ${new Date(paper.uploaded_at).toLocaleDateString('zh-CN')}` : '本地 PDF'}{task ? ` · ${task.stage} · ${statusLabel(task.status)}` : ' · 打开阅读工作台'}</small></span><Icon name="arrow"/></button>
      })}</div>}
    </section>
  </>
}

interface AbstractPreview { ready: boolean; document_hash: string | null; candidate: string; source_text: string; abstract: string; warnings: string[]; note: string }
const normalize = (value: string) => value.trim().replace(/\s+/g, ' ')
export function LocalAbstractPanel({ paper, jobs, modelReady, busy, onSaved, onRun, onJobAction, onSettings }: {
  paper: Paper; jobs: Job[]; modelReady: boolean; busy: string | null; onSaved: (paper: Paper) => void
  onRun: (skill: string) => void; onJobAction: (job: Job, action: 'cancel' | 'retry') => void; onSettings: () => void
}) {
  const [preview, setPreview] = useState<AbstractPreview | null>(null)
  const [draft, setDraft] = useState(paper.abstract)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const parsed = jobs.filter(job => job.paper_id === paper.id && ['fulltext_fetch', 'local_source_fetch'].includes(job.skill_id) && terminalStatus(job.status)).map(job => job.id + job.status).join(',')
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    void api<AbstractPreview>(`/local-papers/${paper.id}/abstract`, {signal:controller.signal}).then(data => {
      setPreview(data); setDraft(data.abstract || data.candidate)
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [paper.id, paper.abstract_revision, parsed, refresh])
  async function save() {
    if (!preview?.document_hash) return
    setSaving(true); setError('')
    try { onSaved(await api<Paper>(`/local-papers/${paper.id}/abstract`, write('PUT', {text:draft, document_hash:preview.document_hash}))) }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setSaving(false) }
  }
  const dirty = normalize(draft) !== normalize(paper.abstract)
  const currentJobs = jobs.filter(job => job.paper_id === paper.id && job.mode === 'live' && job.scope === 'abstract' && job.abstract_revision === paper.abstract_revision && ['paper_reader', 'paper_translator'].includes(job.skill_id))
  return <section className="detail-section local-abstract" aria-label="本地摘要分析与翻译">
    <div className="section-heading"><h3>摘要分析与翻译</h3><button className="button small" onClick={() => setRefresh(value => value + 1)} disabled={loading || saving}>刷新原文</button></div>
    {loading ? <p role="status">正在读取摘要来源…</p> : preview?.ready ? <>
      <p className="fineprint">{preview.note}</p>
      {!paper.abstract && <p className="notice">{preview.candidate ? '已按摘要标题识别候选内容，请核对后保存。' : '未自动识别明确的摘要。请展开已解析原文，复制其中的连续摘要文本；不会将正文自动当成摘要。'}</p>}
      <details><summary>查看已解析原文（前 3 页）</summary><pre className="local-source-excerpt">{preview.source_text}</pre></details>
      {preview.warnings.length > 0 && <p className="fineprint">PDF 解析有警告，请同时核对下方全文工作台和原 PDF。</p>}
      <label className="field-label">核对原文摘要<textarea rows={7} value={draft} onChange={event => setDraft(event.target.value)} placeholder="从已解析原文复制摘要，保留原文内容。" maxLength={12000}/></label>
      <div className="button-row"><button className="button" disabled={saving || normalize(draft).length < 40 || (!dirty && !!paper.abstract)} onClick={() => void save()}>{saving ? '正在保存…' : paper.abstract && !dirty ? '摘要已核对保存' : '确认并保存原文摘要'}</button></div>
      {paper.abstract_source && <p className="fineprint">来源：{paper.abstract_source.location} · 用户核对的 PDF 摘录</p>}
    </> : <p className="notice">请先等待 PDF 解析完成；失败或取消的解析任务可在下方工作台重试。</p>}
    {error && <p className="error-text" role="alert">{error}</p>}
    <div className="button-row"><button className="button primary" disabled={!paper.abstract || dirty || !modelReady || busy === 'paper_reader'} onClick={() => onRun('paper_reader')}>解读摘要</button><button className="button" disabled={!paper.abstract || dirty || !modelReady || busy === 'paper_translator'} onClick={() => onRun('paper_translator')}>翻译摘要</button></div>
    {!modelReady && <p className="notice">模型未配置：可上传、解析和核对摘要。<button className="text-button" onClick={onSettings}>前往研究偏好配置模型 →</button></p>}
    {dirty && paper.abstract && <p className="fineprint">摘要有未保存的修改，保存后可提交新任务。已有全文译文和检查点不变。</p>}
    {currentJobs.filter((job,index) => currentJobs.findIndex(other => other.skill_id === job.skill_id) === index).map(job => <JobCard key={job.id} job={job} busy={busy === job.id} onAction={action => onJobAction(job,action)}/>)}
  </section>
}
