import { useCallback, useEffect, useRef, useState } from 'react'
import { api, dateLabel, errorMessage, statusLabel, timeLabel, write } from './api'
import type { ConferenceDiscovery, Feedback, Job, Mode, Paper } from './types'
import { FeedbackControl, feedbackLabels } from './FeedbackControl'
import { PublicationBadge } from './ReadingWorkspace'

type Page = 'subscriptions' | 'weekly' | 'feedback'
interface SubscriptionDraft {
  name: string; topic: string; query: string; source: 'ccfa_first' | 'arxiv'; days: number; limit: number
  model_enabled: boolean; model_limit: number; weekday: number; hour: number; enabled: boolean; include_keywords: string[]; exclude_keywords: string[]
}
interface Subscription extends SubscriptionDraft { id: string; next_run: string | null; revision: number }
interface ReportSummary { id: string; name: string; week: string; status: string; stage: string; progress: number; error?: string; paper_count?: number; new_count?: number; read_at: string | null }
interface Report { discovery?: ConferenceDiscovery; stale?: boolean; source_fetched_at?: string; id: string; name: string; week: string; note: string; query: string; window_start: string; window_end: string; warnings: string[]; deduplication: string; recall_limit: string; paper_count: number; new_count: number; verified_count: number; generated_at: string; papers: (Paper & {new_to_subscription: boolean; weekly_semantic?: {status:string;message?:string;result?:{score:number;interests:{interest:string;reason:string;evidence:{text:string;location:string}[]}[];unknowns:string[]}};weekly_rank?:{score:number;method:string;feedback_adjustment:number}})[] }
interface FeedbackRow { paper_id: string; value: Feedback['value']; reason: string; updated_at: string; context: {title: string} }
const initialDraft = (): SubscriptionDraft => ({ name: 'Agent 强化学习周报', topic: 'Agent 强化学习', query: '', source: 'ccfa_first', days: 730, limit: 10, weekday: 0, hour: 9, model_enabled: false, model_limit: 5, enabled: false, include_keywords: [], exclude_keywords: [] })
const weekdays = ['周一','周二','周三','周四','周五','周六','周日']
const draftOf = (s: Subscription): SubscriptionDraft => ({ name:s.name,topic:s.topic,query:s.query,source:s.source,days:s.days,limit:s.limit,weekday:s.weekday,hour:s.hour,enabled:s.enabled,model_enabled:s.model_enabled??false,model_limit:s.model_limit??5,include_keywords:s.include_keywords,exclude_keywords:s.exclude_keywords })

export function StageThree({ page, mode, jobs, modelReady, onJob, onOpen, onTasks, onSettings, onFeedback }: {
  page: Page; mode: Mode; jobs: Job[]; modelReady: boolean; onJob: (job: Job) => void; onOpen: (paper: Paper) => void; onTasks: () => void; onSettings: () => void; onFeedback: (id: string, mode: Mode, feedback: Feedback) => void
}) {
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([])
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [evaluation, setEvaluation] = useState<{label_count:number;paired_count:number;note:string;metrics:null|{keyword:{precision_at_10:number};semantic:{precision_at_10:number}}}|null>(null)
  const [feedback, setFeedback] = useState<FeedbackRow[]>([])
  const [report, setReport] = useState<Report | null>(null)
  const [draft, setDraft] = useState(initialDraft)
  const [editing, setEditing] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [online, setOnline] = useState<boolean | null>(null)
  const [refresh, setRefresh] = useState(0)
  const actionPending = useRef(false)
  const createRequest = useRef<{body: string; key: string} | null>(null)
  // SSE already updates the app's durable job snapshots. No simulated progress timer.
  const weeklyChanges = jobs.filter(j => j.skill_id === 'weekly_digest').map(j => j.id + ':' + j.status).join('|')
  const load = useCallback(async (signal: AbortSignal) => {
    setLoading(true); setError('')
    try {
      if (page === 'subscriptions') {
        const result = await api<{subscriptions: Subscription[]; worker: {online: boolean}}>('/subscriptions', {signal})
        setSubscriptions(result.subscriptions); setOnline(result.worker.online)
      } else if (page === 'weekly') {
        const result = await api<{reports: ReportSummary[]}>('/weekly-reports?limit=20&offset=' + offset, {signal})
        setReports(result.reports)
      } else {
        const result = await api<{feedback: FeedbackRow[]}>('/feedback?limit=20&offset=' + offset + '&mode=' + mode, {signal})
        setFeedback(result.feedback)
        setEvaluation(await api('/feedback/evaluation?mode='+mode,{signal}))
      }
    } catch (cause) { if (!signal.aborted) setError(errorMessage(cause)) }
    finally { if (!signal.aborted) setLoading(false) }
  }, [page, mode, offset])
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load, refresh, weeklyChanges])
  async function action(run: () => Promise<void>) {
    if (actionPending.current) return
    actionPending.current = true
    setBusy(true); setError(''); setNotice('')
    try { await run() } catch (cause) { setError(errorMessage(cause)) } finally { actionPending.current = false; setBusy(false) }
  }
  async function openReport(id: string) {
    await action(async () => {
      const result = await api<Report>('/weekly-reports/' + id)
      setReport(result)
      await api('/weekly-reports/' + id + '/read', write('PUT'))
      setRefresh(n => n + 1)
    })
  }
  async function openPaper(paperId: string) {
    await action(async () => { const paper = await api<Paper>('/papers/' + encodeURIComponent(paperId) + '?mode=' + mode); onOpen(paper) })
  }
  function reset() { setEditing(null); setDraft(initialDraft()); createRequest.current = null }
  const liveOnly = mode !== 'live' && page !== 'feedback'
  return <div className="stage-three">
    <section className="section-banner"><h2>{page === 'subscriptions' ? '按主题定期检索' : page === 'weekly' ? '本期论文与推荐依据' : '查看和撤回反馈'}</h2>
      <p>{page === 'feedback' ? '反馈保留原始匹配分，可随时撤回；不会自动修改研究偏好。' : '优先核实 CCF A 正式会议论文，未找到时回退 arXiv；默认只纳入首次公开近两年的论文。'}</p></section>
    <div className="button-row"><button className="button" disabled={busy || loading} onClick={() => report ? void openReport(report.id) : setRefresh(n => n + 1)}>刷新列表</button><button className="button" onClick={onTasks}>查看任务进度</button>{!modelReady && <button className="button" onClick={onSettings}>配置模型</button>}</div>
    {!modelReady && <p className="notice">模型未配置：真实检索、规则周报和反馈可用；模型精读与翻译需配置后主动运行。</p>}
    {liveOnly && <p className="notice">主题订阅和周报使用真实来源。请切换顶部 Live 模式；演示模式不会提交订阅任务。</p>}
    {error && <p className="error-text" role="alert">{error}</p>}{notice && <p className="notice" role="status">{notice}</p>}
    {page === 'subscriptions' && !liveOnly && <>
      <form className="settings-card" onSubmit={e => { e.preventDefault(); void action(async () => {
        const body = JSON.stringify(draft)
        if (!editing && createRequest.current?.body !== body) createRequest.current = {body, key:crypto.randomUUID()}
        const request = write(editing ? 'PUT' : 'POST', draft)
        if (!editing && createRequest.current) request.headers = {'Idempotency-Key':createRequest.current.key}
        const saved = await api<Subscription>('/subscriptions' + (editing ? '/' + editing : ''), request)
        setNotice(saved.enabled ? '订阅已启用，下次到期由 worker 生成。' : '已保存为暂停；可手动生成本期周报。')
        reset(); setRefresh(n => n + 1)
      }) }}>
        <fieldset className="subscription-fields" disabled={busy} aria-label="订阅设置">
        <div className="section-heading"><h2>{editing ? '编辑主题订阅' : '创建主题订阅'}</h2></div>
        <div className="button-row">{['Agent 强化学习','大模型后训练'].map(topic => <button className="chip" type="button" key={topic} onClick={() => setDraft(d => ({...d, topic, name:topic + '周报', query:''}))}>{topic}</button>)}</div>
        <div className="field-grid"><label className="field-label">订阅名称<input required maxLength={100} value={draft.name} onChange={e => setDraft({...draft,name:e.target.value})}/></label><label className="field-label">研究方向<input required maxLength={160} value={draft.topic} onChange={e => setDraft({...draft,topic:e.target.value,query:''})}/></label></div>
        <label className="field-label">英文检索式（可编辑）<textarea rows={3} maxLength={4000} value={draft.query} placeholder="留空时按研究方向生成英文关键词；保存后可查看实际表达式" onChange={e => setDraft({...draft,query:e.target.value})}/></label>
        <button type="button" className="button small" disabled={busy} onClick={() => void action(async () => { const catalog = await api<{tracks:{name:string;query:string}[]}>('/research-modules'); const preset=catalog.tracks.find(track=>track.name===draft.topic); const r = preset ? {query:preset.query,warnings:['主题检索式可编辑；未调用模型。']} : await api<{query:string;warnings:string[]}>('/query',write('POST',{text:draft.topic}));setDraft(d => ({...d,query:r.query}));setNotice(r.warnings.join('；')) })}>生成英文检索式</button>
        <div className="field-grid"><label className="field-label">来源优先级<select value={draft.source} onChange={e => setDraft({...draft,source:e.target.value as SubscriptionDraft['source']})}><option value="ccfa_first">CCF A 核实优先 · arXiv 回退</option><option value="arxiv">arXiv</option></select></label><label className="field-label">首次公开日期<select value={draft.days} onChange={e => setDraft({...draft,days:Number(e.target.value)})}>{[[730,'近两年（默认）'],[365,'近一年'],[90,'近 90 天'],[30,'近 30 天']].map(([n,label]) => <option key={n} value={n}>{label}</option>)}</select></label></div>
        <div className="field-grid"><label className="field-label">必须包含（逗号分隔）<input value={draft.include_keywords.join(',')} onChange={e => setDraft({...draft,include_keywords:e.target.value.split(/[,，]/)})}/></label><label className="field-label">排除关键词<input value={draft.exclude_keywords.join(',')} onChange={e => setDraft({...draft,exclude_keywords:e.target.value.split(/[,，]/)})}/></label></div>
        <div className="field-grid"><label className="field-label">每周生成时间（北京时间）<div className="button-row"><select value={draft.weekday} onChange={e => setDraft({...draft,weekday:Number(e.target.value)})}>{weekdays.map((day,i) => <option key={day} value={i}>{day}</option>)}</select><select aria-label="生成小时" value={draft.hour} onChange={e => setDraft({...draft,hour:Number(e.target.value)})}>{Array.from({length:24},(_,n) => <option key={n} value={n}>{String(n).padStart(2,'0')}:00</option>)}</select></div></label><label className="field-label">每期最多收录<input type="number" min={1} max={30} value={draft.limit} onChange={e => setDraft({...draft,limit:Number(e.target.value)})}/></label></div>
        <label className="subscription-optin"><input type="checkbox" checked={draft.model_enabled} onChange={e=>setDraft({...draft,model_enabled:e.target.checked})}/>启用模型语义评估（可能产生模型调用费用）</label>
        {draft.model_enabled && <label className="field-label">每期最多语义评估候选数（1–10，含缓存复用）<input type="number" min={1} max={10} value={draft.model_limit} onChange={e=>setDraft({...draft,model_limit:Number(e.target.value)})}/></label>}
        <p className="fineprint">只评估本订阅主题，保留关键词分。相同来源、模型与主题复用结果；未配置模型、失败或超出上限时降级。开启后也适用于定时周报。</p>
        <label className="subscription-optin"><input type="checkbox" checked={draft.enabled} onChange={e => setDraft({...draft,enabled:e.target.checked})}/>启用每周订阅</label>
        <p className="fineprint">需独立 worker 持续运行。错过的计划在重启后补生成当前周一次，不补发所有历史周。仅站内周报，不发送邮件；只有主动开启模型增强后才按上限调用模型。</p>
        <div className="button-row"><button className="button primary" disabled={busy} type="submit">{busy ? '处理中…' : '保存订阅'}</button>{editing && <button className="button" type="button" onClick={reset}>取消编辑</button>}</div>
        </fieldset>
      </form>
      {online === false && <p className="notice">worker 当前离线；已保存的计划与任务不会丢失，启动 worker 后继续。</p>}
      {loading ? <p role="status">正在读取订阅…</p> : !subscriptions.length ? <p className="notice">暂无订阅。创建并勾选开启后，系统才会按计划检索。</p> : subscriptions.map(s => <article className="settings-card" key={s.id}>
        <div className="section-heading"><h3>{s.name}</h3><span className={'status-pill ' + (s.enabled ? 'succeeded' : 'queued')}>{s.enabled ? '已启用' : '已暂停'}</span></div>
        <p>{s.model_enabled ? `模型增强 · 最多 ${s.model_limit} 个候选` : '规则周报 · 不调用模型'}</p><p>{s.topic} · {s.source === 'ccfa_first' ? 'CCF A 优先 / arXiv 回退' : 'arXiv'} · 近 {s.days} 天 · 最多 {s.limit} 篇</p>
        <p>{weekdays[s.weekday]} {String(s.hour).padStart(2,'0')}:00（北京时间） · 下次：{s.next_run ? timeLabel(s.next_run) : '暂停中'}</p><code className="subscription-query">{s.query}</code>
        <div className="button-row"><button className="button primary" disabled={busy} onClick={() => void action(async () => { const r=await api<{job:Job;message:string}>('/subscriptions/'+s.id+'/run',write('POST'));onJob(r.job);setNotice(r.message);setRefresh(n=>n+1) })}>生成本期周报</button><button className="button" disabled={busy} onClick={() => void action(async () => { await api('/subscriptions/'+s.id+'/enabled',write('PUT',{enabled:!s.enabled}));setRefresh(n=>n+1) })}>{s.enabled ? '暂停订阅' : '开启订阅'}</button><button className="button" disabled={busy} onClick={() => { setEditing(s.id);setDraft(draftOf(s));window.scrollTo({top:0,behavior:'smooth'}) }}>编辑</button></div>
      </article>)}
    </>}
    {page === 'weekly' && !liveOnly && <>
      {report ? <section><div className="button-row"><button className="button" onClick={() => setReport(null)}>← 返回周报列表</button><a className="button primary" href={'/api/jobs/'+report.id+'/artifact'} download>下载周报 Markdown</a></div>
        <h2>{report.name} · {report.week}</h2><p>{report.paper_count} 篇 · 新收录 {report.new_count} 篇 · 核实 CCF A {report.verified_count} 篇</p><p>{report.note}</p>
        <details className="settings-card"><summary>检索范围与来源说明</summary><p>首次公开：{dateLabel(report.window_start)} 至 {dateLabel(report.window_end)} · 生成于 {timeLabel(report.generated_at)}</p><code className="subscription-query">{report.query}</code><p>{report.deduplication}</p><p>{report.recall_limit}</p><p>来源获取于 {timeLabel(report.source_fetched_at || report.generated_at)}{report.stale ? ' · 过期缓存，请复核来源' : ''}</p>{report.discovery?.sources?.map((source,i)=><p key={i}>{source.source} · {source.status === 'available' ? '可用' : '暂不可用'}{source.message ? ' · '+source.message : ''}</p>)}{report.discovery?.queries?.map((query,i)=><code className="subscription-query" key={i}>{query}</code>)}{report.warnings.map((w,i)=><p className="notice" key={i}>{w}</p>)}</details>
        {!report.papers.length && <p className="notice">本期没有符合条件的论文。请查看检索范围与来源说明。</p>}
        {report.papers.map(p => <article className="paper-card" key={p.id}><div className="paper-meta"><span className="tag">{p.new_to_subscription ? '本期新收录' : '曾收录'}</span><span>首次公开 {dateLabel(p.published)} · v{p.version}</span></div><button className="paper-title" disabled={busy} onClick={() => void openPaper(p.id)}>{p.title}</button><PublicationBadge publication={p.publication}/><p>{p.recommendation?.reason}</p><p>本期关键词分 {p.recommendation?.score} · 当前反馈调整 {p.feedback?.adjustment ?? 0}（后续检索生效，本期顺序保留）</p>
          {p.weekly_semantic?.result ? <section><h4>本订阅主题语义匹配 · {p.weekly_semantic.result.score} 分</h4>{p.weekly_semantic.result.interests.map(item=><div key={item.interest}><p>{item.interest}：{item.reason}</p>{item.evidence.map((e,i)=><blockquote key={i}>{e.text}<footer>{e.location}</footer></blockquote>)}</div>)}<p className="fineprint">{p.weekly_semantic.result.unknowns.join('；')}</p></section> : <p className="fineprint">{p.weekly_semantic?.message || '历史规则周报'}</p>}{p.weekly_rank && <p>本期排序分 {p.weekly_rank.score} · {p.weekly_rank.method==='semantic'?'语义':'关键词'}基础分 + 当时反馈 {p.weekly_rank.feedback_adjustment}；CCF A 优先级保持。</p>}
          {p.recommendation?.evidence.map((e,i)=><blockquote key={i}>{e.text}<footer>{e.location} · 摘要覆盖</footer></blockquote>)}<p className="fineprint">尚待核实：{p.recommendation?.unknowns.join('；')}</p>
          <div className="button-row"><button className="button" disabled={busy} onClick={() => void openPaper(p.id)}>打开论文 · 阅读与翻译</button><a className="button" href={p.url} target="_blank" rel="noreferrer">原文来源</a></div><FeedbackControl paper={p} onChange={feedback => { onFeedback(p.id, p.mode, feedback); setReport(current => current ? {...current,papers:current.papers.map(item => item.id === p.id ? {...item,feedback} : item)} : null) }}/>
        </article>)}
      </section> : <>{loading ? <p role="status">正在读取周报…</p> : !reports.length ? <p className="notice">暂无周报。请在主题订阅中主动生成一期，或开启每周计划。</p> : reports.map(r => {
        const current=jobs.find(j=>j.id===r.id)
        return <article className="settings-card" key={r.id}><div className="section-heading"><h3>{r.name} · {r.week}</h3><span className="tag">{r.read_at?'已读':'未读'}</span></div><p>{statusLabel(current?.status??r.status)} · {current?.stage??r.stage} · {current?.progress??r.progress}%</p>{r.error&&<p className="error-text">{r.error}</p>}<p>{r.paper_count??'—'} 篇 · 新收录 {r.new_count??'—'} 篇</p><button className="button primary" disabled={busy || (current?.status??r.status)!=='succeeded'} onClick={()=>void openReport(r.id)}>阅读周报</button></article>
      })}</>}
    </>}
    {page === 'feedback' && <>{evaluation && <section className="settings-card"><h3>推荐效果对照</h3><p>近两年明确相关性标注 {evaluation.label_count} 篇 · 已有当前模型/兴趣语义对照 {evaluation.paired_count}/30 篇；建议积累 30–50 篇。</p><p className="fineprint">{evaluation.note}</p>{evaluation.metrics ? <p>已标注候选集前 10 篇相关比例：关键词 {(evaluation.metrics.keyword.precision_at_10*100).toFixed(0)}% · 语义 {(evaluation.metrics.semantic.precision_at_10*100).toFixed(0)}%</p>:<p>可对照样本不足，暂不显示效果指标。</p>}<a className="button" href={'/api/feedback/evaluation.json?mode='+mode} download>下载标注与评分对照</a></section>}{loading ? <p role="status">正在读取反馈…</p> : !feedback.length ? <p className="notice">暂无反馈。在论文卡片或周报中选择“推荐反馈”即可记录判断。</p> : feedback.map(f=><article className="settings-card" key={f.paper_id}><h3>{f.context.title}</h3><p>{feedbackLabels[f.value]} · {timeLabel(f.updated_at)}</p><p>{f.reason || '未填写原因'}</p><div className="button-row"><button className="button" disabled={busy} onClick={()=>void openPaper(f.paper_id)}>查看论文</button><button className="button" disabled={busy} onClick={()=>void action(async()=>{const result=await api<Feedback>('/feedback/'+encodeURIComponent(f.paper_id)+'?mode='+mode,write('PUT',{value:'none'}));onFeedback(f.paper_id,mode,result);setNotice('已撤回反馈；原研究偏好和收藏不变。');setRefresh(n=>n+1)})}>撤回反馈</button></div></article>)}</>}
    {page !== 'subscriptions' && !liveOnly && !report && <div className="pagination"><button className="button" disabled={loading || offset===0} onClick={()=>setOffset(n=>Math.max(0,n-20))}>上一页</button><span>第 {offset/20+1} 页</span><button className="button" disabled={loading || (page==='weekly'?reports.length:feedback.length)<20} onClick={()=>setOffset(n=>n+20)}>下一页</button></div>}
  </div>
}


