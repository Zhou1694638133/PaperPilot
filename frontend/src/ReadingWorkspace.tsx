import { translationAction } from './translationAction'
import { OverallAnalysis } from './OverallAnalysis'
import { TranslationDiagnostics, TranslationPrecheck } from './TranslationDiagnostics'
import { useEffect, useRef, useState } from 'react'
import { api, errorMessage, statusLabel, terminalStatus, write } from './api'
import { EmptyState, Icon } from './components'
import { TranslationDownloads } from './TranslationDownloads'
import type { Alignment, Job, Mode, Paper, PaperDocument, Publication, Reading } from './types'

export function PublicationBadge({ publication }: { publication?: Publication }) {
  if (publication?.status !== 'verified') return <span className="tag neutral">录用未核实</span>
  return <a className="tag ccf-tag" href={publication.evidence_url || undefined} target="_blank" rel="noreferrer" title={publication.evidence_text}>CCF A · {publication.venue} {publication.year} ↗</a>
}

export function ResearchModules({ onSearch }: { onSearch: (query: string, name: string) => void }) {
  const [data, setData] = useState<{tracks: {id: string; name: string; description: string; keywords: string[]; query: string}[]; ccf: {coverage: string; source_url: string; edition: string; venues: string[]}} | null>(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState('')
  useEffect(() => { let active = true; void api<NonNullable<typeof data>>('/research-modules').then(value => { if (active) setData(value) }).catch(cause => { if (active) setError(errorMessage(cause)) }); return () => { active = false } }, [])
  if (error) return <p className="notice" role="alert">研究模块加载失败：{error}</p>
  if (!data) return <p className="fineprint">正在加载研究模块…</p>
  const track = data.tracks.find(item => item.id === selected)
  return <section className="research-modules"><div className="section-heading"><h3>重点研究模块</h3><span className="subtle-badge">各方向优先 CCF A · 默认近两年</span></div>
    <div className="module-grid topic-module-grid">{data.tracks.map(item => <button key={item.id} className={selected === item.id ? 'active' : ''} onClick={() => { setSelected(item.id); onSearch(item.query, item.name) }}><Icon name="node"/><strong>{item.name}</strong><small>{item.description}</small></button>)}</div>
    {track && <p className="module-keywords">研究轴线：{track.keywords.join(' · ')}<br/>建议精读：训练信号 → 轨迹与信用分配 → 评测环境 → 泛化与局限。仅为阅读提纲，不代表论文结论。</p>}
    <p className="fineprint"><a href={data.ccf.source_url} target="_blank" rel="noreferrer">{data.ccf.edition} ↗</a> · {data.ccf.coverage} 相关预印本保留为补充；未核实录用的论文不会标记 CCF A。排序偏好不代表质量概率。</p>
  </section>
}

const fields = [['research_question', '研究问题'], ['method', '方法'], ['experiments', '实验陈述'], ['limitations', '局限'], ['research_relation', '研究关联']] as const

export function ReadingWorkspace({ paper, jobs, modelReady, busy, onRun, onJobAction }: {
  paper: Paper; jobs: Job[]; modelReady: boolean; busy: string | null
  onRun: (skill: string, scope: 'abstract' | 'fulltext', acknowledge: boolean, translationJobId?: string) => void
  onJobAction: (job: Job, action: 'cancel' | 'retry') => void
}) {
  const [document, setDocument] = useState<PaperDocument | null>(null)
  const [reading, setReading] = useState<Reading | null>(null)
  const [alignment, setAlignment] = useState<Alignment | null>(null)
  const [tab, setTab] = useState<'source' | 'reading' | 'bilingual' | 'chinese' | 'analysis'>('source')
  const [section, setSection] = useState('all')
  const [acknowledge, setAcknowledge] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const relevant = jobs.filter(job => job.paper_id === paper.id && job.mode === paper.mode && job.scope === 'fulltext' && (!job.paper_version || job.paper_version === paper.version))
  const translation = relevant.find(job => job.skill_id === 'paper_translator')
  const active = relevant.find(job => !terminalStatus(job.status))
  const completed = relevant.filter(job => job.status === 'succeeded').map(job => job.id).join(',')
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    void api<{document: PaperDocument | null; reading: Reading | null}>(`/fulltext?${new URLSearchParams({paper_id: paper.id, mode: paper.mode})}`, {signal: controller.signal}).then(value => { setDocument(value.document); setReading(value.reading) }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [paper.id, paper.mode, paper.version, completed, refresh])
  useEffect(() => {
    if (!translation) { setAlignment(null); return }
    const controller = new AbortController()
    void api<Alignment>(`/jobs/${translation.id}/alignment`, {signal: controller.signal}).then(setAlignment).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [translation?.id, translation?.updated_at, refresh])
  useEffect(() => { setAcknowledge(false) }, [document?.document_hash])
  const requiresReview = !!document && document.status !== 'ready'
  const available = !loading && !!document && modelReady && paper.mode === 'live' && (!requiresReview || acknowledge)
  const currentAlignment = !!document && document.paper_id === paper.id && document.version === paper.version && alignment?.job.id === translation?.id && alignment?.document_hash === document.document_hash
  const translatedView = tab === 'bilingual' || tab === 'chinese'
  const blocks = (translatedView && currentAlignment && alignment ? alignment.blocks : document?.blocks || []).filter(block => section === 'all' || block.section === section)
  const downloadReady = !loading && currentAlignment && translation?.status === 'succeeded' && !!alignment && alignment.total > 0 && alignment.completed === alignment.total
  const downloadStatus = loading ? '正在核对论文版本与翻译状态…' : downloadReady
    ? `当前论文 v${paper.version} · ${alignment?.completed}/${alignment?.total} 块已完成 · PDF 适合阅读，Markdown 方便编辑。`
    : !translation ? '尚未生成全文译文，完成翻译后即可下载。'
    : !currentAlignment ? '正在核对翻译检查点；只有与当前全文版本一致的完整译文可以下载。'
    : `全文翻译${statusLabel(translation.status)}，已完成 ${alignment?.completed ?? 0}/${alignment?.total ?? 0} 块。完整下载将在翻译完成后开放。`
  const action = translationAction({ loading, hasDocument: !!document, modelReady, live: paper.mode === 'live', requiresReview, acknowledged: acknowledge, active: !!active, submitting: busy === 'paper_translator', complete: downloadReady, status: translation?.status, currentAlignment, canRetry: translation?.can_retry })
  function startTranslation() {
    if (action.disabled) return
    setTab('chinese')
    if (action.action === 'retry' && translation) onJobAction(translation, 'retry')
    else if (action.action === 'start') onRun('paper_translator', 'fulltext', acknowledge)
  }
  const translateButton = <button className="button primary" disabled={action.disabled} title={action.reason || undefined} onClick={startTranslation}><Icon name="translate" size={15}/>{action.label}</button>
  return <section className="fulltext-workspace detail-section"><div className="section-heading"><div><h3>全文研究工作台</h3></div><button className="icon-button" aria-label="刷新全文状态" onClick={() => setRefresh(value => value + 1)}><Icon name="refresh"/></button></div>
    <div className="button-row"><button className="button primary" disabled={paper.mode !== 'live' || busy === 'fulltext_fetch' || !!active} onClick={() => onRun('fulltext_fetch', 'fulltext', false)}>{document ? '检查全文任务' : '读取全文'}<Icon name="book" size={15}/></button><button className="button" disabled={!available || !!active || busy === 'paper_reader'} onClick={() => { setTab('reading'); onRun('paper_reader', 'fulltext', acknowledge) }}>结构化精读</button><button className="button" disabled={!document} onClick={() => setTab('analysis')}>论文总体分析</button>{translateButton}{document && <a className="button" href={`/api/reports/pdf?${new URLSearchParams({paper_id: paper.id, mode: paper.mode, scope: 'fulltext'})}`} download>导出原文解析 PDF</a>}</div>
    <div className="translation-start-panel" aria-label="全文翻译状态">
      <p>{downloadReady ? '全文翻译已完成，中文翻译和双语对照共用同一份译文。' : !translation ? `尚未开始全文翻译。${document ? `已解析 ${document.paragraph_count} 块原文，提交后由 worker 逐段翻译并保存检查点。` : '请先读取全文。'}` : downloadStatus}</p>
      {requiresReview && <label className="review-check"><input type="checkbox" checked={acknowledge} onChange={event => setAcknowledge(event.target.checked)}/>我已核对解析警告，同意仅翻译已解析内容；缺失内容不猜补。</label>}
      {action.reason && <p className="notice" role="status">{action.reason}{!modelReady && <a href="#preferences"> 打开研究偏好 →</a>}</p>}
    </div>
    {!modelReady && <p className="notice">模型未配置：可真实读取全文、维护术语表、对比和导出；精读与翻译需要后端模型密钥，当前不生成模型结果。</p>}
    {paper.mode === 'demo' && <p className="notice">演示模式不下载全文或调用模型，请切换真实模式。</p>}
    {error && <p className="error-text" role="alert">{error}</p>}
    {loading && <p role="status">正在读取持久全文状态…</p>}
    {relevant.filter(job => !['paper_analysis','analysis_pdf'].includes(job.skill_id)).filter((job, index, relevant) => relevant.findIndex(other => other.skill_id === job.skill_id) === index).slice(0, 3).map(job => <div className="reading-job" key={job.id}><strong>{job.skill_id === 'chinese_pdf' ? '中文论文排版' : job.skill_id === 'local_source_fetch' ? '恢复原图与源文件' : job.skill_id === 'fulltext_fetch' ? '全文获取' : job.skill_id === 'paper_reader' ? '全文精读' : '全文翻译'} · {statusLabel(job.status)}</strong><span>{job.stage} · {job.progress}%</span>{job.error && <p className="error-text">{job.error}</p>}<TranslationDiagnostics job={job}/><div className="button-row">{!terminalStatus(job.status) && <button className="button small" disabled={busy === job.id} onClick={() => onJobAction(job, 'cancel')}>取消</button>}{['failed', 'partial', 'cancelled', 'needs_confirmation'].includes(job.status) && <button className="button small" disabled={busy === job.id || !(job.can_retry ?? (job.attempts < 3))} onClick={() => onJobAction(job, 'retry')}>从检查点继续{job.requires_confirmation ? '（需确认费用）' : ''}</button>}{job.status === 'succeeded' && <><a className="button small" href={`/api/jobs/${job.id}/artifact`} download>Markdown</a><a className="button small" href={`/api/reports/pdf?job_id=${job.id}`} download>PDF</a></>}</div></div>)}
    {paper.source === 'local' && document?.method !== 'latex' && <section className="translation-downloads" aria-label="恢复图文排版"><h4>保留原图、公式、表格与算法</h4><p>从 PDF 首页识别带版本的 arXiv 标识，核对同版本正文并恢复结构化源文件，使用与在线论文相同的中文／双语排版模板。</p><button className="button primary" disabled={!!active || busy === 'local_source_fetch'} onClick={() => onRun('local_source_fetch', 'fulltext', false)}>恢复原图与源文件</button><p className="fineprint">此步骤不调用模型。原文件和旧译文保留，完全匹配的正文检查点可复用；无法匹配的段落需重新翻译。未找到源文件或版本不一致时明确报错，不会以文字版替代图文版。</p></section>}
    <TranslationPrecheck paperId={paper.id} mode={paper.mode} ready={!!document}/>
    {document ? <><div className="document-facts"><span>{document.method === 'latex' ? 'LaTeX 结构化源文件' : paper.source === 'local' ? '本地 PDF 文本解析' : 'PDF 文本回退'}</span><span>{document.paragraph_count} 个段落/保护块</span><span>{document.blocks.filter(block => block.translatable).length} 个正文块逐块调用模型</span><span>v{document.version} · parser {document.parser_version}</span><a href={document.source_url} target="_blank" rel="noreferrer">解析来源 ↗</a></div><p className="fineprint">{document.note}</p>
      {document.warnings.length > 0 && <details className="parse-warnings" open><summary>解析警告 · {document.warnings.length} 项</summary><ul>{document.warnings.map(item => <li key={item}>{item}</li>)}</ul></details>}
      {document.source_enrichment && <details className="parse-warnings" open><summary>来源补全 · 宏还原与参考文献</summary><ul>{document.source_enrichment.notes.map(note => <li key={note}>{note}</li>)}</ul><a href={document.source_enrichment.pdf_url} target="_blank" rel="noreferrer">查看原 PDF 来源 ↗</a></details>}
      <div className="reading-tabs">{([['source','原文阅读'], ['reading','结构化精读'], ['analysis','总体分析'], ['chinese','中文翻译'], ['bilingual','双语对照']] as const).map(([id,label]) => <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</div>
      {tab === 'analysis' ? <OverallAnalysis paper={paper} jobs={relevant} documentHash={document.document_hash} available={available} busy={busy} onRun={onRun} onJobAction={onJobAction} onEvidence={section => { setSection(section); setTab('source') }}/> : tab === 'reading' ? reading?.coverage === 'fulltext' ? <div className="structured-reading">{fields.map(([key, label]) => <section key={key}><h4>{label}</h4><p>{reading[key].text}</p>{reading.evidence_locations?.filter(item => item.field === key).map((item, index) => <blockquote key={index}><p>{item.quote}</p><button className="text-button" onClick={() => { setSection(item.section); setTab('source') }}>{item.paragraph_id} · {item.location} · 查看原文 →</button></blockquote>)}</section>)}<p className="notice">{reading.unknowns.join('；')}</p></div> : <p className="notice">尚无当前版本的全文精读结果。{modelReady ? '提交任务后，按原文块生成带位置的结构化结果。' : '请先配置模型。'}</p> : <>
        {translatedView && <><div className="button-row">{translateButton}</div><TranslationDownloads variant={tab === 'chinese' ? 'chinese' : 'bilingual'} key={`${tab}-${paper.mode}-${paper.id}-${paper.version}-${translation?.id}`} jobId={translation?.id} ready={downloadReady} status={downloadStatus} typesetAvailable={document.method === 'latex'} modelReady={modelReady} busy={!!active || busy === 'chinese_pdf' || busy === 'local_source_fetch'} onRestoreSource={paper.source === 'local' ? () => onRun('local_source_fetch', 'fulltext', false) : undefined} chineseJob={relevant.find(job => job.skill_id === 'chinese_pdf' && job.translation_job_id === translation?.id)} onTypeset={() => onRun('chinese_pdf', 'fulltext', true, translation?.id)} onJobAction={onJobAction}/><p className="notice">{currentAlignment && alignment ? alignment.supplemental_count ? `原译文检查点 ${alignment.checkpoint_completed}/${alignment.checkpoint_total} 块；补充参考文献 ${alignment.supplemental_count} 条（原文保留，未调用模型）· 术语表 v${alignment.glossary_version}。` : `已完成 ${alignment.completed}/${alignment.total} 块 · 术语表 v${alignment.glossary_version}。未完成部分保留空白；公式、表格、代码与参考文献保护块保留原文。` : '尚无当前全文版本的翻译检查点。点击“开始全文翻译”后，已完成段落会陆续显示。'}</p></>}
        <label className="section-filter">章节<select aria-label="选择全文章节" value={section} onChange={event => setSection(event.target.value)}><option value="all">全部章节</option>{[...new Map(document.blocks.map(block => [block.section, block.display_section || block.section])).entries()].map(([name, label]) => <option key={name} value={name}>{label}</option>)}</select></label>
        <div className="aligned-blocks">{blocks.map(block => <article className="aligned-block" key={block.id}><header><strong>{block.id} · {block.display_section || block.section}</strong><span>{block.location}</span></header>{block.source_url && <a href={block.source_url} target="_blank" rel="noreferrer">核对原 PDF 页码 ↗</a>}<div className={tab === 'bilingual' ? 'bilingual-grid' : ''}>{tab !== 'chinese' && <div><small>原文 · {block.supplemental ? 'PDF 参考文献补充' : block.translatable ? '正文' : '保护块 / 仅保留'}{block.display_text && ' · 宏已按定义展开'}</small><p className={block.kind === 'prose' || block.kind === 'reference' ? 'source-prose' : 'source-code'}>{block.display_text || block.text}</p></div>}{translatedView && <div><small>{block.status === 'preserved' ? '原文保留 · 未调用模型' : block.translation ? '中文译文 · 已保存检查点' : '中文译文 · 待翻译'}</small><p className={block.translation ? 'translated-prose' : 'translation-pending'}>{block.display_translation || block.translation || (block.failure ? `未完成：${block.failure.error || block.failure.reason}` : '此段尚未生成译文。')}</p></div>}</div>{tab !== 'chinese' && (block.display_text || block.display_translation) && <details><summary>查看原始 LaTeX 与检查点（未改写）</summary><p className="source-code">{block.text}</p>{tab === 'bilingual' && block.translation && <p className="translated-prose">{block.translation}</p>}</details>}</article>)}</div>
      </>}
    </> : !loading && <p className="muted">{paper.source === 'local' ? '本地 PDF 由独立 worker 解析，无需再次下载。完成后核对警告，再点击“开始全文翻译”。' : '点击“读取全文”后，由独立 worker 下载并解析。LaTeX 源文件优先，PDF 回退会明确标注缺页、乱码或顺序风险。'}</p>}
  </section>
}

export function GlossaryEditor({ modelReady, jobs, onJobs }: { modelReady: boolean; jobs: Job[]; onJobs: (jobs: Job[]) => void }) {
  const [text, setText] = useState('')
  const [version, setVersion] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  async function load() { try { const data = await api<{version:number;terms:Record<string,string>}>('/glossary'); setVersion(data.version); setText(Object.entries(data.terms).map(([a,b]) => `${a} = ${b}`).join('\n')); setMessage('已读取持久术语表。') } catch(cause) { setMessage(errorMessage(cause)) } }
  useEffect(() => { void load() }, [])
  async function learn() {
    setBusy(true)
    try {
      const data = await api<{eligible_count:number;jobs:Job[];warnings:string[]}>('/glossary/learn', write('POST'))
      onJobs(data.jobs)
      const pending = data.jobs.filter(job => !terminalStatus(job.status)).length
      setMessage(`找到 ${data.eligible_count} 篇已标记真实论文，${pending} 个任务待完成。已有任务会复用；失败任务请在任务中心重试。完成后点击“重新加载”查看术语。${data.warnings.join('；')}`)
    } catch(cause) { setMessage(errorMessage(cause)) } finally { setBusy(false) }
  }
  async function save() {
    setBusy(true)
    try {
      const terms: Record<string,string> = {}
      for (const line of text.split('\n').filter(line => line.trim())) {
        const at = line.indexOf('='); const source = line.slice(0, at).trim(); const target = line.slice(at + 1).trim()
        if (at < 1 || !target) throw new Error('每行请使用“英文术语 = 中文译法”。')
        if (Object.keys(terms).some(key => key.toLowerCase() === source.toLowerCase())) throw new Error(`术语重复：${source}`)
        terms[source] = target
      }
      const data = await api<{version:number}>('/glossary', write('PUT', {version, terms})); setVersion(data.version); setMessage(`已保存术语表 v${data.version}。新任务使用此版本；已有任务保持原术语快照，避免混译。`)
    } catch(cause) { setMessage(errorMessage(cause)) } finally { setBusy(false) }
  }
  return <section className="settings-card"><h2>共享术语表 · v{version || '…'}</h2><p className="muted">收藏、已读或添加标签/笔记后，自动从真实论文的标题与摘要积累专业术语；不感兴趣和演示论文不参与。提取使用已配置模型，可能产生调用费用。</p><p className="muted">合并时去重并保留已有译法，原文依据和译法冲突可在任务中心查看。最多保存 100 条，超出部分保留在任务产物中。新翻译使用最新术语表，已有任务保持原快照；请等待提取完成后再创建翻译任务。</p><div className="button-row"><button className="button" disabled={busy || !modelReady} onClick={() => void learn()}>从已标记论文补充术语</button><a className="button" href="#jobs">查看提取任务与原文依据</a></div>{!modelReady && <p className="notice">请先在研究偏好配置模型，再补充历史论文术语。</p>}{jobs.filter(job => job.skill_id === 'glossary_extractor').slice(0, 10).map(job => <p className="fineprint" key={job.id}>{job.paper_id} · {statusLabel(job.status)} · {job.stage}{job.error ? ` · ${job.error}` : ''}</p>)}<label className="field-label">每行一个术语：英文 = 中文<textarea className="glossary-input" rows={18} value={text} disabled={busy || !version} onChange={event => setText(event.target.value)} spellCheck={false}/></label><div className="button-row"><button className="button primary" disabled={busy || !version} onClick={() => void save()}>保存新版本</button><button className="button" disabled={busy} onClick={() => void load()}>重新加载</button></div>{message && <p className="notice" role="status">{message}</p>}</section>
}

interface Comparison { papers: {id:string;title:string;url:string;published:string;version:number;publication:Publication;coverage:string;reading:Reading|null;abstract:string;fulltext_status:string}[]; warning:string }
export function ComparisonWorkspace({ papers, mode, onRemove, onOpen }: {papers:Paper[]; mode:Mode; onRemove:(id:string)=>void; onOpen:(paper:Paper)=>void}) {
  const [result,setResult] = useState<Comparison | null>(null)
  const [error,setError] = useState('')
  const [busy,setBusy] = useState(false)
  const ids = papers.map(paper=>paper.id).join(',')
  const comparisonRequest = useRef<AbortController | null>(null)
  useEffect(() => {
    setResult(null); setError(''); setBusy(false)
    return () => { comparisonRequest.current?.abort() }
  }, [ids,mode])
  async function compare() {
    comparisonRequest.current?.abort()
    const controller = new AbortController()
    comparisonRequest.current = controller
    setBusy(true); setError('')
    try {
      const next = await api<Comparison>('/compare', {...write('POST', {ids:papers.map(p=>p.id), mode}), signal:controller.signal})
      if (!controller.signal.aborted) setResult(next)
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorMessage(cause))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }
  return <><section className="section-banner"><h2>对比论文内容与精读结果</h2><p>从发现页或详情选择 2–4 篇论文。展示论文信息和已有精读结果；不同实验基准的分数不直接排名。</p></section><div className="comparison-selection">{papers.map(paper=><div key={paper.id}><button className="text-button" onClick={()=>onOpen(paper)}>{paper.title}</button><button className="icon-button" aria-label={`从对比移除 ${paper.title}`} onClick={()=>onRemove(paper.id)}><Icon name="close"/></button></div>)}</div><div className="button-row"><button className="button primary" disabled={papers.length<2||busy} onClick={()=>void compare()}>{busy?'正在读取证据…':'生成/刷新对比'}</button>{papers.length>=2&&<a className="button" href={`/api/reports/pdf?${new URLSearchParams({ids,mode})}`} download>导出对比 PDF</a>}</div>{error&&<p className="error-text" role="alert">{error}</p>}{papers.length<2&&<EmptyState title="再选择论文开始对比" icon="book"><p>已选择 {papers.length}/4 篇。选择内容保留至本次页面刷新。</p></EmptyState>}{result&&<><p className="notice">{result.warning}</p><div className="comparison-grid">{result.papers.map(paper=><article className="comparison-paper" key={paper.id}><PublicationBadge publication={paper.publication}/><h3><a href={paper.url} target="_blank" rel="noreferrer">{paper.title} ↗</a></h3><p className="fineprint">首次公开 {paper.published.slice(0,10)} · v{paper.version}<br/>覆盖：{paper.coverage === 'fulltext' ? '已解析全文精读' : paper.coverage === 'abstract' ? '摘要精读' : '元数据与摘要'}</p>{fields.slice(0,4).map(([key,label])=><section key={key}><h4>{label}</h4><p>{paper.reading?.[key].text || '尚未生成精读结果，未核实。'}</p>{paper.reading?.[key].evidence.map((quote,index)=><blockquote key={index}>{quote}</blockquote>)}</section>)}<details><summary>摘要原文</summary><p>{paper.abstract}</p></details></article>)}</div></>}</>
}
