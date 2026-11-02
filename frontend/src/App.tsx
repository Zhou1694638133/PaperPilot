import { applyPaperFeedback } from './feedbackState'
import { isOlderJob, mergeJobs } from './jobState'
import { LocalAbstractPanel, LocalPapersPage } from './LocalPapersPage'
import { ResearchOrbit } from './ResearchOrbit'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, dateLabel, errorMessage, exportUrl, statusLabel, terminalStatus, timeLabel, write } from './api'
import { EmptyState, EvidencePanel, Icon, JobCard, PaperCard, ResultContent, SkillCard } from './components'
import type { Collection, Feedback, Health, Job, Mode, Paper, Profile, SearchResult, Skill, View } from './types'
import { StageThree } from './StageThree'
import { ModelSettings } from './ModelSettings'
import { SemanticMatch } from './SemanticMatch'
import { ComparisonWorkspace, GlossaryEditor, PublicationBadge, ReadingWorkspace, ResearchModules } from './ReadingWorkspace'

const defaultProfile: Profile = { interests: ['多智能体协作', 'Agent 评测', '需求理解', 'Agent 强化学习', '大模型后训练'], include_keywords: [], exclude_keywords: [], days: 730, page_size: 10, language: 'zh-CN' }
const navigation: { id: View; icon: string; name: string; caption: string }[] = [
  { id: 'discover', icon: 'compass', name: '发现论文', caption: '按研究兴趣检索论文，查看匹配理由和摘要。' },
  { id: 'local', icon: 'translate', name: '本地论文', caption: '上传你的论文，在这里完成摘要分析、全文精读、翻译与下载。' },
  { id: 'subscriptions', icon: 'clock', name: '主题订阅', caption: '设置研究主题和每周检索时间，可随时编辑或暂停。' },
  { id: 'weekly', icon: 'book', name: '站内周报', caption: '查看每周检索结果、推荐理由和来源。' },
  { id: 'feedback', icon: 'check', name: '推荐反馈', caption: '记录个人判断，保留关键词与语义匹配分作为对照。' },
  { id: 'library', icon: 'book', name: '我的文库', caption: '管理收藏论文、阅读状态、标签和笔记。' },
  { id: 'compare', icon: 'node', name: '论文对比', caption: '对比论文的研究问题、方法和实验结果。' },
  { id: 'glossary', icon: 'translate', name: '术语表', caption: '统一 Agent 强化学习与后训练术语，锁定每次翻译的版本。' },
  { id: 'skills', icon: 'grid', name: 'Skills 工作台', caption: '选择论文分析、翻译和导出功能。' },
  { id: 'jobs', icon: 'jobs', name: '任务中心', caption: '查看任务进度、下载结果或重试未完成部分。' },
  { id: 'preferences', icon: 'settings', name: '研究偏好', caption: '设置研究兴趣、筛选条件和模型服务。' },
]
const initialView = (): View => navigation.some(n => n.id === location.hash.slice(1)) ? location.hash.slice(1) as View : 'discover'
const splitList = (text: string) => text.split(/[,，\n]/).map(item => item.trim()).filter(Boolean)

export default function App() {
  const [view, setView] = useState<View>(initialView)
  const [mode, setMode] = useState<Mode>('live')
  const [health, setHealth] = useState<Health | null>(null)
  const [profile, setProfile] = useState<Profile>(defaultProfile)
  const [profileDraft, setProfileDraft] = useState<Profile>(defaultProfile)
  const [skills, setSkills] = useState<Skill[]>([])
  const [jobs, setJobState] = useState<Job[]>([])
  const jobsRef = useRef<Job[]>([])
  function setJobs(update: Job[] | ((previous: Job[]) => Job[])) {
    const incoming = typeof update === 'function' ? update(jobsRef.current) : update
    const merged = mergeJobs(jobsRef.current, incoming)
    jobsRef.current = merged
    setJobState(merged)
  }
  const [papers, setPapers] = useState<Paper[]>([])
  const [library, setLibrary] = useState<Paper[]>([])
  const [comparison, setComparison] = useState<Paper[]>([])
  const [selected, setSelected] = useState<Paper | null>(null)
  const [result, setResult] = useState<SearchResult | null>(null)
  const [naturalQuery, setNaturalQuery] = useState('多智能体协作与 Agent 评测')
  const [queryDraft, setQueryDraft] = useState('(all:"multi-agent" OR all:"agent collaboration") AND (all:"large language model" OR all:"LLM")')
  const [appliedQuery, setAppliedQuery] = useState('(all:"multi-agent" OR all:"agent collaboration") AND (all:"large language model" OR all:"LLM")')
  const [queryMessage, setQueryMessage] = useState('基于英文关键词检索；可编辑检索表达式。')
  const [showQuery, setShowQuery] = useState(false)
  const [days, setDays] = useState(730)
  const [sort, setSort] = useState('ccfa')
  const [start, setStart] = useState(0)
  const [searchVersion, setSearchVersion] = useState(0)
  const [ready, setReady] = useState(false)
  const [loading, setLoading] = useState(true)
  const [libraryLoading, setLibraryLoading] = useState(true)
  const [libraryError, setLibraryError] = useState('')
  const libraryRequest = useRef(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [queryBusy, setQueryBusy] = useState(false)
  const [semanticBusy, setSemanticBusy] = useState(false)
  const [navOpen, setNavOpen] = useState(false)
  const [copilotOpen, setCopilotOpen] = useState(false)
  const [copilotTab, setCopilotTab] = useState<'evidence' | 'reader' | 'translator'>('evidence')
  const [copilotQuestion, setCopilotQuestion] = useState('')
  const [librarySearch, setLibrarySearch] = useState('')
  const [libraryFilter, setLibraryFilter] = useState('all')
  const [notes, setNotes] = useState('')
  const [tags, setTags] = useState('')
  const [streamState, setStreamState] = useState('暂无活动任务')
  const searchRef = useRef<HTMLInputElement>(null)
  const cursors = useRef<Record<string, string>>({})
  const modeRef = useRef(mode)
  modeRef.current = mode

  function replacePaper(updated: Paper) {
    const replace = (items: Paper[]) => items.map(item => item.id === updated.id && item.mode === updated.mode ? updated : item)
    setPapers(replace); setLibrary(replace); setComparison(replace)
    setSelected(previous => previous?.id === updated.id && previous.mode === updated.mode ? updated : previous)
  }
  function applyFeedback(paperId: string, paperMode: Mode, feedback: Feedback) {
    const update = (paper: Paper): Paper => paper.id === paperId && paper.mode === paperMode
      ? applyPaperFeedback(paper, feedback)
      : paper
    setPapers(items => items.map(update)); setLibrary(items => items.map(update)); setComparison(items => items.map(update))
    setSelected(paper => paper ? update(paper) : paper)
    if (modeRef.current === paperMode) setNotice(feedback.value === 'none' ? '已撤回反馈，发现页排序/隐藏状态已更新。' : '推荐反馈已保存，发现页排序/隐藏状态已更新。')
  }
  async function ignorePaper(paper: Paper) {
    setBusy(paper.id)
    try {
      const feedback = await api<Feedback>('/feedback/' + encodeURIComponent(paper.id) + '?mode=' + paper.mode, write('PUT', {value:'not_relevant'}))
      applyFeedback(paper.id, paper.mode, feedback)
      setNotice('已隐藏，可在推荐反馈页面撤回。')
    } catch (cause) { setNotice(errorMessage(cause)) } finally { setBusy(null) }
  }
  async function refreshPaper(paperId: string, paperMode: Mode) {
    try {
      const updated = await api<Paper>(`/papers/${encodeURIComponent(paperId)}?mode=${paperMode}`)
      if (modeRef.current === paperMode) {
        replacePaper(updated)
        const job = updated.semantic?.job
        if (job) setJobs(previous => [job, ...previous.filter(item => item.id !== job.id)])
      }
    } catch (cause) { setNotice(`评分状态刷新失败，可刷新连接重试：${errorMessage(cause)}`) }
  }
  async function runSemantic(targets: Paper[]) {
    if (!targets.length) return
    setSemanticBusy(true)
    try {
      const response = await api<{jobs: Job[]}>('/semantic/rank', write('POST', {paper_ids: targets.slice(0, 10).map(paper => paper.id), mode}))
      setJobs(previous => [...response.jobs, ...previous.filter(item => !response.jobs.some(job => job.id === item.id))])
      await Promise.all(targets.slice(0, 10).map(paper => refreshPaper(paper.id, paper.mode)))
      setNotice(response.jobs.length ? '语义评分已提交或复用已有结果；匹配排序优先使用已完成的语义分，其余使用关键词分。' : '语义模型当前不可用，已自动使用关键词评分。')
    } catch (cause) { setNotice(`语义评分不可用，保留关键词分：${errorMessage(cause)}`) }
    finally { setSemanticBusy(false) }
  }

  function navigate(next: View) { if (next === 'discover') { setSelected(previous => previous?.source === 'local' ? papers[0] || null : previous); setCopilotTab('evidence') } if (next === 'local') { setSelected(previous => previous?.source === 'local' ? previous : null); setCopilotOpen(false) } setView(next); location.hash = next; setNavOpen(false); window.scrollTo({ top: 0 }) }
  useEffect(() => {
    function onHash() { const hash = location.hash.slice(1); if (navigation.some(n => n.id === hash) || hash === 'detail') setView(hash as View) }
    function onKey(event: KeyboardEvent) { if ((event.ctrlKey || event.metaKey) && event.key === 'k') { event.preventDefault(); navigate('discover'); searchRef.current?.focus() } if (event.key === 'Escape') { setCopilotOpen(false); setNavOpen(false) } }
    window.addEventListener('hashchange', onHash); window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('hashchange', onHash); window.removeEventListener('keydown', onKey) }
  }, [])
  useEffect(() => {
    let disposed = false
    void Promise.allSettled([api<Health>('/health'), api<Profile>('/profile'), api<{ skills: Skill[] }>('/skills'), api<{ jobs: Job[] }>('/jobs')]).then(([h, p, s, j]) => {
      if (disposed) return
      if (h.status === 'fulfilled') { setHealth(h.value); setMode(h.value.mode) }
      if (p.status === 'fulfilled') { setProfile(p.value); setProfileDraft(p.value); setDays(p.value.days) }
      if (s.status === 'fulfilled') setSkills(s.value.skills)
      if (j.status === 'fulfilled') setJobs(j.value.jobs)
      if (h.status === 'rejected') setNotice('后端暂不可用。请启动 API 和 worker，然后点击刷新连接。')
      setReady(true)
    })
    return () => { disposed = true }
  }, [])
  useEffect(() => {
    if (!ready) return
    let disposed = false
    let pending: AbortController | null = null
    async function checkHealth() {
      if (disposed || pending) return
      const controller = new AbortController()
      pending = controller
      const timeout = window.setTimeout(() => controller.abort(), 8000)
      try {
        const current = await api<Health>('/health', { signal: controller.signal, cache: 'no-store' })
        if (!disposed) setHealth(current)
      } catch {
        if (!disposed) setHealth(null)
      } finally {
        window.clearTimeout(timeout)
        pending = null
      }
    }
    function onVisible() { if (document.visibilityState === 'visible') void checkHealth() }
    void checkHealth()
    const timer = window.setInterval(() => { void checkHealth() }, 10000)
    window.addEventListener('focus', onVisible)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      disposed = true
      window.clearInterval(timer)
      window.removeEventListener('focus', onVisible)
      document.removeEventListener('visibilitychange', onVisible)
      pending?.abort()
    }
  }, [ready])
  useEffect(() => {
    if (!ready || view !== 'discover') return
    const controller = new AbortController()
    setLoading(true); setError('')
    const params = new URLSearchParams({ query: appliedQuery, mode, days: String(days), start: String(start), limit: String(profile.page_size), sort })
    void api<SearchResult>(`/papers?${params}`, { signal: controller.signal }).then(data => {
      setPapers(data.papers); setResult(data)
      setSelected(previous => previous?.mode === mode ? data.papers.find(p => p.id === previous.id) || previous : data.papers[0] || null)
    }).catch(cause => { if (!controller.signal.aborted) { setError(errorMessage(cause)); setPapers([]); setResult(null) } }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [ready, view, appliedQuery, mode, days, sort, start, profile.page_size, searchVersion])
  async function refreshLibrary() {
    const requestedMode = mode
    const requestId = ++libraryRequest.current
    setLibraryLoading(true)
    setLibraryError('')
    try {
      const data = await api<{ papers: Paper[] }>(`/collections?mode=${requestedMode}`)
      if (modeRef.current === requestedMode && libraryRequest.current === requestId) setLibrary(data.papers)
    } catch (cause) { if (modeRef.current === requestedMode && libraryRequest.current === requestId) setLibraryError(errorMessage(cause)) }
    finally { if (modeRef.current === requestedMode && libraryRequest.current === requestId) setLibraryLoading(false) }
  }
  useEffect(() => { if (ready) void refreshLibrary() }, [ready, mode, view === 'library'])
  useEffect(() => { if (selected) { setNotes(selected.collection.notes); setTags(selected.collection.tags.join(', ')) } }, [selected?.id, selected?.mode])
  useEffect(() => { if (selected) void refreshPaper(selected.id, selected.mode) }, [selected?.id, selected?.mode, profile.interests.join('\n'), health?.model, health?.model_configured])
  useEffect(() => {
    if (!ready) return
    let disposed = false
    let pending: AbortController | null = null
    async function syncTasks() {
      if (disposed || pending) return
      const controller = new AbortController()
      pending = controller
      const timeout = window.setTimeout(() => controller.abort(), 8000)
      try {
        const snapshot = await api<{jobs: Job[]}>('/jobs', { signal: controller.signal, cache: 'no-store' })
        if (!disposed) setJobs(snapshot.jobs)
      } catch { /* SSE continues; the next snapshot reconciles missed events. */ }
      finally { window.clearTimeout(timeout); pending = null }
    }
    function onVisible() { if (document.visibilityState === 'visible') void syncTasks() }
    void syncTasks()
    const timer = window.setInterval(() => { void syncTasks() }, 30000)
    window.addEventListener('focus', onVisible)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      disposed = true
      window.clearInterval(timer)
      window.removeEventListener('focus', onVisible)
      document.removeEventListener('visibilitychange', onVisible)
      pending?.abort()
    }
  }, [ready])
  const activeJobIds = jobs.filter(job => !terminalStatus(job.status)).map(job => job.id).sort().join(',')
  useEffect(() => {
    if (!activeJobIds) { setStreamState('暂无活动任务'); return }
    const streams = activeJobIds.split(',').map(id => {
      const stream = new EventSource(`/api/jobs/${id}/events${cursors.current[id] ? `?after=${cursors.current[id]}` : ''}`)
      stream.onopen = () => setStreamState('SSE 已连接 · 真实后端事件')
      stream.onmessage = event => {
        try {
          const job = JSON.parse(event.data) as Job
          if (!job.id) return
          if (event.lastEventId) cursors.current[id] = event.lastEventId
          if (isOlderJob(job, jobsRef.current.find(item => item.id === job.id))) return
          setJobs(previous => previous.map(item => item.id === job.id ? job : item))
          if (job.skill_id === 'semantic_ranker' && job.mode) {
            if (terminalStatus(job.status)) void refreshPaper(job.paper_id, job.mode)
            else {
              const update = (paper: Paper): Paper => paper.semantic?.job?.id === job.id
                ? {...paper, semantic: {...paper.semantic, status: job.status, job}} : paper
              setPapers(previous => previous.map(update)); setLibrary(previous => previous.map(update))
              setSelected(previous => previous ? update(previous) : previous)
            }
          }
          if (terminalStatus(job.status)) stream.close()
        } catch { setStreamState('事件格式异常，请刷新任务快照。') }
      }
      stream.onerror = () => setStreamState('连接中断，正在自动重连；任务仍在后端执行。')
      return stream
    })
    return () => streams.forEach(stream => stream.close())
  }, [activeJobIds])
  async function refreshConnection() {
    setBusy('connection')
    try {
      const [h, s, j] = await Promise.all([api<Health>('/health'), api<{skills: Skill[]}>('/skills'), api<{jobs: Job[]}>('/jobs')])
      setHealth(h); setSkills(s.skills); setJobs(j.jobs); setNotice('已刷新服务连接与任务快照。')
      if (selected) await refreshPaper(selected.id, selected.mode)
    } catch (cause) { setNotice(errorMessage(cause)) } finally { setBusy(null) }
  }
  async function prepareQuery(text = naturalQuery, run = false) {
    if (!text.trim()) { setNotice('请先输入研究兴趣或英文关键词。'); return }
    setQueryBusy(true)
    try {
      const data = await api<{query: string; keywords: string[]; method: string; warnings?: string[]}>('/query', write('POST', { text }))
      setQueryDraft(data.query); setShowQuery(true)
      setQueryMessage(data.warnings?.length ? data.warnings.join('；') : `${data.method} · ${data.keywords.join(' / ')} · 可手动调整`)
      if (run && data.query) { setAppliedQuery(data.query); setStart(0); setSearchVersion(version => version + 1); navigate('discover') }
    } catch (cause) { setNotice(errorMessage(cause)) } finally { setQueryBusy(false) }
  }
  function runSearch(event?: FormEvent) {
    event?.preventDefault()
    if (!queryDraft.trim()) { setNotice('请输入有效的英文检索表达式。'); return }
    setAppliedQuery(queryDraft.trim()); setStart(0); setSearchVersion(version => version + 1)
  }
  function changeMode(next: Mode) { setMode(next); setSelected(null); setPapers([]); setLibrary([]); setLibraryError(''); setLibraryLoading(true); setResult(null); setStart(0) }
  function choosePaper(paper: Paper, openDetail = true) { setSelected(paper); if (openDetail) navigate(paper.source === 'local' ? 'local' : 'detail'); if (paper.source !== 'local') setCopilotTab('evidence'); if (paper.source === 'local') { void refreshPaper(paper.id, paper.mode); requestAnimationFrame(() => document.getElementById('local-paper-detail')?.scrollIntoView({behavior:'smooth', block:'start'})) } }
  async function updateCollection(paper: Paper, patch: Partial<Collection>) {
    setBusy(paper.id)
    try {
      const updated = await api<Paper>(`/collections/${encodeURIComponent(paper.id)}?mode=${paper.mode}`, write('PUT', patch))
      if (updated.terminology?.job) {
        const job = updated.terminology.job
        setJobs(previous => [job, ...previous.filter(item => item.id !== job.id)])
      }
      if (modeRef.current !== paper.mode) return
      setPapers(previous => previous.map(item => item.id === updated.id ? updated : item))
      setSelected(previous => previous?.id === updated.id ? updated : previous)
      setLibrary(previous => updated.collection.saved ? [...previous.filter(item => item.id !== updated.id), updated] : previous.filter(item => item.id !== updated.id))
      setNotice(patch.interested === false ? '已标记不感兴趣。下次检索将应用此反馈。' : patch.notes !== undefined ? '笔记与标签已保存到文库。' : patch.saved === true ? '已收藏，重启后仍会保留。' : patch.saved === false ? '已取消收藏。' : '阅读状态已更新。')
      if (updated.terminology?.message) setNotice(previous => `${previous} ${updated.terminology?.message}`)
    } catch (cause) { setNotice(errorMessage(cause)) } finally { setBusy(null) }
  }
  function addComparison(paper: Paper) {
    if (comparison.some(item => item.id === paper.id && item.mode === paper.mode)) { navigate('compare'); return }
    if (comparison.filter(item => item.mode === mode).length >= 4) { setNotice('最多对比 4 篇论文，请先移除一篇。'); return }
    setComparison(previous => [...previous.filter(item => item.mode === mode), paper]); setNotice('已加入对比，可在“论文对比”页面查看。')
  }
  async function runSkill(skillId: string, scope: 'abstract' | 'fulltext' = 'abstract', acknowledge = false, translationJobId?: string) {
    if (!selected) { setNotice('请先在发现论文或文库中选择一篇论文。'); return }
    let analysisJobId: string | undefined
    if (['paper_analysis', 'local_source_fetch'].includes(skillId)) scope = 'fulltext'
    if (skillId === 'analysis_pdf') {
      scope = 'fulltext'
      analysisJobId = translationJobId || jobs.find(job => job.skill_id === 'paper_analysis' && job.paper_id === selected.id && job.mode === mode && job.paper_version === selected.version && job.status === 'succeeded')?.id
      translationJobId = undefined
      if (!analysisJobId) { setNotice('请先在论文详情完成总体分析，再生成分析 PDF。'); return }
    }
    if (skillId === 'semantic_ranker') { await runSemantic([selected]); return }
    if (skillId === 'chinese_pdf' && !translationJobId) {
      translationJobId = jobs.find(job => job.skill_id === 'paper_translator' && job.paper_id === selected.id && job.mode === mode && job.paper_version === selected.version && job.scope === 'fulltext' && job.status === 'succeeded')?.id
      if (!translationJobId) { setNotice('请先在论文详情完成当前版本的全文翻译，再生成中文版 PDF。'); return }
      scope = 'fulltext'
    }
    if (!['recommendation_explainer', 'fulltext_fetch', 'local_source_fetch', 'analysis_pdf'].includes(skillId) && (mode === 'demo' || !health?.model_configured)) { setNotice(mode === 'demo' ? '演示模式不调用付费模型。请切换真实模式并配置后端模型。' : '模型未配置。请在研究偏好中填写模型地址、模型名称与 API Key。'); return }
    setBusy(skillId)
    try {
      const job = await api<Job>('/jobs', write('POST', { skill_id: skillId, paper_id: selected.id, mode, scope, acknowledge_partial: acknowledge, translation_job_id: translationJobId, analysis_job_id: analysisJobId }))
      setJobs(previous => [job, ...previous.filter(item => item.id !== job.id)])
      if (scope === 'abstract' && skillId !== 'fulltext_fetch' && selected.source !== 'local') setCopilotOpen(true)
      setNotice('任务已提交到持久队列。进度由独立 worker 推送。')
      setCopilotTab(skillId === 'paper_translator' ? 'translator' : skillId === 'paper_reader' ? 'reader' : 'evidence')
    } catch (cause) { setNotice(errorMessage(cause)) } finally { setBusy(null) }
  }
  async function jobAction(job: Job, action: 'cancel' | 'retry') {
    if (action === 'retry' && (job.status === 'needs_confirmation' || job.requires_confirmation) && !window.confirm('上一次模型调用可能已经产生费用。重试可能重复计费，是否继续？')) return
    setBusy(job.id)
    try {
      const updated = await api<Job>(`/jobs/${job.id}/${action}`, write('POST', (job.status === 'needs_confirmation' || job.requires_confirmation) ? { confirm_duplicate_cost: true } : undefined))
      setJobs(previous => previous.map(item => item.id === updated.id ? updated : item))
      if (job.skill_id === 'semantic_ranker' && job.mode) await refreshPaper(job.paper_id, job.mode)
    } catch (cause) {
      try {
        const current = await api<Job>(`/jobs/${job.id}`, { cache: 'no-store' })
        setJobs([current])
        setNotice(action === 'retry' && !terminalStatus(current.status)
          ? '任务已经在排队或执行，已同步最新进度，无需重复恢复。'
          : action === 'retry' && current.status === 'succeeded'
            ? '任务已完成，已同步结果。' : errorMessage(cause))
      } catch { setNotice(errorMessage(cause)) }
    } finally { setBusy(null) }
  }
  async function saveProfile(event: FormEvent) {
    event.preventDefault(); setBusy('profile')
    try {
      const cleaned = { ...profileDraft, interests: profileDraft.interests.map(value => value.trim()).filter(Boolean), include_keywords: splitList(profileDraft.include_keywords.join(',')), exclude_keywords: splitList(profileDraft.exclude_keywords.join(',')) }
      const saved = await api<Profile>('/profile', write('PUT', cleaned))
      setProfile(saved); setProfileDraft(saved); setDays(saved.days); setStart(0); setSearchVersion(version => version + 1); setNotice('研究偏好已保存。后续推荐将按新偏好计算。')
    } catch (cause) { setNotice(errorMessage(cause)) } finally { setBusy(null) }
  }
  const localContext = ['subscriptions', 'weekly', 'feedback'].includes(view) || view === 'local' || (view !== 'discover' && selected?.source === 'local')
  const currentNav = navigation.find(item => item.id === view)
  const visibleLibrary = library.filter(paper => (libraryFilter === 'all' || (libraryFilter === 'read' ? paper.collection.read : !paper.collection.read)) && `${paper.title} ${paper.abstract} ${paper.collection.tags.join(' ')}`.toLowerCase().includes(librarySearch.toLowerCase()))
  const scoreFor = (paper: Paper) => (paper.semantic?.result?.score ?? paper.recommendation?.score ?? 0) + (paper.feedback?.adjustment ?? 0)
  const rankedPapers = papers.filter(paper => paper.feedback?.value !== 'not_relevant' && paper.collection.interested).sort((a, b) => {
    if (sort === 'newest') return b.published.localeCompare(a.published)
    if (sort === 'ccfa') {
      const priority = Number(b.publication?.status === 'verified') - Number(a.publication?.status === 'verified')
      if (priority) return priority
    }
    return scoreFor(b) - scoreFor(a) || (b.recommendation?.score ?? 0) - (a.recommendation?.score ?? 0)
  })
  const visiblePapers = view === 'library' ? visibleLibrary : rankedPapers
  const currentJobs = jobs.filter(job => !job.mode || job.mode === mode)
  const selectedJob = currentJobs.find(job => job.paper_id === selected?.id && job.scope !== 'fulltext' && job.skill_id === (copilotTab === 'translator' ? 'paper_translator' : copilotTab === 'reader' ? 'paper_reader' : 'recommendation_explainer'))
  const modelUnavailable = !health?.model_configured || mode === 'demo'
  return <div className="app">
    {navOpen && <button className="scrim nav-scrim" aria-label="关闭导航" onClick={() => setNavOpen(false)}/>}
    <aside className={`sidebar ${navOpen ? 'open' : ''}`}>
      <a className="brand" href="#discover" onClick={() => navigate('discover')}><span className="brand-mark"><Icon name="compass" size={31}/></span><span><strong>PaperPilot</strong><small>论文领航</small></span></a>
      <div className="nav-label">工作台</div>
      <nav>{navigation.filter(item => item.id !== 'preferences').map(item => <button key={item.id} className={`nav-item ${view === item.id || (view === 'detail' && item.id === 'discover') ? 'active' : ''}`} onClick={() => navigate(item.id)}><Icon name={item.icon}/><span>{item.name}</span>{item.id === 'library' && <small>{libraryLoading || libraryError ? '—' : library.length}</small>}{item.id === 'jobs' && currentJobs.filter(job => !terminalStatus(job.status)).length > 0 && <small>{currentJobs.filter(job => !terminalStatus(job.status)).length}</small>}</button>)}</nav>
      <div className="nav-divider"/><div className="nav-label">研究方向</div>
      <div className="topic-nav">{profile.interests.map((interest, i) => <button key={interest} onClick={() => { setNaturalQuery(interest); void prepareQuery(interest, true) }} disabled={queryBusy}><i className={`topic-dot dot-${i % 3}`}/><span>{interest}</span><span className="topic-arrow">↗</span></button>)}<button className="add-interest" onClick={() => navigate('preferences')}>＋ 管理研究方向</button></div>
      <div className="sidebar-bottom"><div className="workspace-status"><span><i className={`status-dot ${health ? '' : 'offline'}`}/>{health ? '本地研究工作台' : '等待后端连接'}</span><p>{!health ? '后端未连接 · worker 状态未知' : health.worker?.online ? 'worker 在线 · 单用户模式' : 'worker 未连接 · 任务将排队'}</p></div><button className={`nav-item ${view === 'preferences' ? 'active' : ''}`} onClick={() => navigate('preferences')}><Icon name="settings"/><span>研究偏好</span></button><div className="researcher"><span className="avatar">R</span><div>我的研究空间<small>Personal workspace</small></div><span className="mono">LOCAL</span></div></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setNavOpen(true)}><Icon name="menu"/></button><div className="breadcrumb">Workspace<span>/</span><strong>{view === 'detail' ? '论文详情' : currentNav?.name}</strong></div><div className="topbar-right"><span className={`mode-badge ${mode}`}><i/>{mode === 'live' ? '真实数据模式' : '演示数据模式'}</span><label className="mode-switch"><span className="sr-only">切换运行模式</span><select value={mode} onChange={event => changeMode(event.target.value as Mode)}><option value="live">Live</option><option value="demo">Demo</option></select></label><button className="icon-button" title="刷新后端连接" aria-label="刷新后端连接" disabled={busy === 'connection'} onClick={() => void refreshConnection()}><Icon name="refresh"/></button>{!localContext && <button className="button copilot-toggle" onClick={() => setCopilotOpen(true)}><Icon name="sparkle"/>Copilot</button>}</div></header>
      <main className="workspace">
        <div className="page-heading"><div><h1>{view === 'detail' ? '论文详情' : currentNav?.name}</h1><p>{view === 'detail' ? '阅读原文，查看分析、翻译和推荐依据。' : currentNav?.caption}</p></div><div className="heading-actions">{view === 'discover' && <button className="button" onClick={() => navigate('preferences')}><Icon name="settings" size={15}/>调整偏好</button>}{view === 'library' && visibleLibrary.length > 0 && <a className="button" href={exportUrl(visibleLibrary.map(p => p.id), mode, 'markdown')} download><Icon name="download" size={15}/>导出文库</a>}{view === 'jobs' && <button className="button" onClick={() => void refreshConnection()}><Icon name="refresh" size={15}/>刷新快照</button>}</div></div>
        {mode === 'demo' && <div className="demo-notice"><Icon name="info" size={16}/><span>演示模式使用历史论文样例，保留真实年份。匹配结果仅演示规则筛选，不产生模型解读或翻译。</span></div>}
        <div className={`dashboard-grid ${localContext ? 'local-layout' : ''}`}><div className="content-column">
          {view === 'discover' && <>
            <section className="hero"><div className="hero-copy"><h2>大模型与 Agent<br/><span>论文检索与阅读</span></h2><p>默认检索近两年的论文。<br className="mobile-break"/>优先核实 CCF A 会议论文，无结果时使用 arXiv。</p><div className="hero-bottom"><span>关键词与语义匹配</span><span>全文翻译</span><span>主题周报</span></div></div><ResearchOrbit onTopic={topic => void prepareQuery(topic, true)}/></section>
            <div className="metric-strip"><div><Icon name="compass"/><span><small>本页展示</small><strong>{loading ? '—' : rankedPapers.length}<em>篇论文</em></strong></span></div><div><Icon name="bookmark"/><span><small>我的文库</small><strong>{libraryLoading || libraryError ? '—' : library.length}<em>篇已收藏</em></strong></span></div><div><Icon name="node"/><span><small>研究方向</small><strong>{profile.interests.length}<em>个方向</em></strong></span></div></div>
            <form className="search-box" onSubmit={event => { event.preventDefault(); void prepareQuery() }}><Icon name="search" size={20}/><input ref={searchRef} aria-label="研究兴趣或英文关键词" value={naturalQuery} onChange={event => setNaturalQuery(event.target.value)} placeholder="描述你的研究兴趣，或输入英文关键词…"/><button type="submit" className="button primary" disabled={queryBusy}>{queryBusy ? '转换中…' : '生成检索式'}<Icon name="arrow" size={15}/></button></form>
            <div className="query-toggle-row"><span>根据研究兴趣生成英文关键词与同义词</span><button onClick={() => setShowQuery(!showQuery)}>{showQuery ? '收起检索式' : '编辑检索式'}</button></div>
            {showQuery && <form className="query-editor" onSubmit={runSearch}><label htmlFor="query-expression">实际发送至 arXiv 的检索表达式</label><textarea id="query-expression" value={queryDraft} onChange={event => setQueryDraft(event.target.value)} rows={2}/><div><p>{queryMessage}</p><button type="submit" className="button primary" disabled={loading || !queryDraft.trim()}>开始检索<Icon name="search" size={14}/></button></div></form>}
            <div className="section-heading"><h2>为你发现</h2><span className="subtle-badge">语义 + 关键词</span><span className="result-count">{loading ? '正在检索…' : `${rankedPapers.length} 篇 / 来源候选 ${result?.total ?? '—'}`}</span></div>
            <div className="semantic-toolbar"><button className="button" disabled={loading || semanticBusy || !rankedPapers.length || modelUnavailable} onClick={() => void runSemantic(rankedPapers.slice(0, 10))}>{semanticBusy ? '正在提交语义评分…' : '本页语义重排（最多 10 篇）'}</button><p>{modelUnavailable ? '模型未配置或处于演示模式，自动使用关键词分。' : `已完成语义评分 ${rankedPapers.filter(paper => paper.semantic?.result).length}/${rankedPapers.length} 篇；其余使用关键词分。点击会调用模型，重复输入复用结果。`}</p></div><div className="filter-row"><div className="topic-chips"><button className="chip" onClick={() => void prepareQuery('large language model agent', true)}>全部方向</button>{profile.interests.map(interest => <button className={`chip ${naturalQuery === interest ? 'active' : ''}`} key={interest} onClick={() => { setNaturalQuery(interest); void prepareQuery(interest, true) }} disabled={queryBusy}>{interest}</button>)}</div><div className="filter-controls"><label><Icon name="clock" size={13}/><select aria-label="首次公开日期范围" value={days} onChange={event => { setDays(Number(event.target.value)); setStart(0) }}><option value={0}>全部时间</option><option value={7}>最近 7 天</option><option value={30}>最近 30 天</option><option value={90}>最近 90 天</option><option value={365}>最近一年</option><option value={730}>近两年（730 天）</option></select></label><label><select aria-label="排序方式" value={sort} onChange={event => { setSort(event.target.value); setStart(0) }}><option value="ccfa">跨来源检索 · CCF A 优先</option><option value="match">本页匹配排序（语义优先）</option><option value="newest">首次公开时间</option></select></label></div></div><div className="source-caption"><span>{sort === 'ccfa' ? '来源：arXiv + 正式会议论文集 · CCF A 优先' : '来源：arXiv · 按首次公开日期筛选'}</span>{result && <span>{result.cached ? '缓存于' : '获取于'} {timeLabel(result.fetched_at)}</span>}</div>
          </>}
          {view === 'discover' && !loading && result && <>
            {result.warning && <p className="notice" role="status">{result.warning}</p>}
            <details className="search-provenance"><summary>查看本次实际检索式与来源记录</summary><code>{result.query}</code><p>检索记录：<span className="mono">{result.run_id}</span><br/>{result.ranking_scope === 'candidate_pool' ? '在本轮有界候选池中优先 CCF A，再按兴趣匹配排序；并非全网完整结果。' : '仅对当前来源页进行匹配排序。'}日期始终按 arXiv 首次公开时间过滤。</p>{result.discovery && <><p>{result.discovery.note}</p><ul>{result.discovery.sources.map(source => <li key={source.source}>{source.source} · {source.status === 'available' ? `已索引 ${source.count} 条正式记录` : source.message || '暂不可用'}{source.url && <> · <a href={source.url} target="_blank" rel="noreferrer">核对来源 ↗</a></>}</li>)}</ul><p>会议标题补充召回 {result.discovery.recall_candidates} 篇，成功匹配 {result.discovery.added} 篇（合并去重前）。</p>{result.discovery.queries.slice(1).map(query => <code key={query}>{query}</code>)}</>}</details>
            {result.discovery && <div className="discovery-links"><span>继续核对同一研究主题：</span>{result.discovery.links.map(link => <a key={link.url} href={link.url} target="_blank" rel="noreferrer">{link.name} ↗</a>)}<small>外部检索链接，结果不会自动标记为已录用。</small></div>}
          </>}
          {view === 'discover' && <ResearchModules onSearch={(query, name) => { setNaturalQuery(name); setQueryDraft(query); setAppliedQuery(query); setSort('ccfa'); setStart(0); setSearchVersion(value => value + 1) }}/>}
          {view === 'local' && <LocalPapersPage mode={mode} modelReady={!!health?.model_configured} selected={selected} jobs={jobs} onSettings={() => navigate('preferences')} onOpen={paper => choosePaper(paper)} onImported={(paper, job, reused) => {
            setLibrary(previous => [paper, ...previous.filter(item => item.id !== paper.id)])
            setJobs(previous => [job, ...previous.filter(item => item.id !== job.id)])
            choosePaper(paper)
            setNotice(reused ? '相同文件已存在，已复用论文与任务。' : '文件已保存，等待 worker 解析后即可核对摘要并翻译。')
          }}/> }
          {(view === 'subscriptions' || view === 'weekly' || view === 'feedback') && <StageThree key={view + mode} page={view} mode={mode} jobs={jobs} modelReady={!!health?.model_configured} onJob={job => setJobs(previous => [job, ...previous.filter(j => j.id !== job.id)])} onOpen={paper => choosePaper(paper)} onTasks={() => navigate('jobs')} onSettings={() => navigate('preferences')} onFeedback={applyFeedback}/>}
          {view === 'compare' && <ComparisonWorkspace papers={comparison.filter(paper => paper.mode === mode)} mode={mode} onRemove={id => setComparison(previous => previous.filter(paper => paper.id !== id))} onOpen={paper => choosePaper(paper)}/>}
          {view === 'glossary' && <GlossaryEditor modelReady={!!health?.model_configured} jobs={jobs} onJobs={submitted => setJobs(previous => [...submitted, ...previous.filter(item => !submitted.some(job => job.id === item.id))])}/>}
          {view === 'library' && <><div className="library-toolbar"><label className="library-search"><Icon name="search" size={17}/><input aria-label="搜索文库" placeholder="搜索标题、摘要或个人标签" value={librarySearch} onChange={event => setLibrarySearch(event.target.value)}/></label><button className="icon-button" title="刷新文库" aria-label="刷新文库" onClick={() => void refreshLibrary()}><Icon name="refresh"/></button></div><div className="library-tabs">{[['all','全部收藏'],['unread','稍后阅读'],['read','已读论文']].map(([id, label]) => <button className={libraryFilter === id ? 'active' : ''} key={id} onClick={() => setLibraryFilter(id)}>{label}</button>)}<span>{libraryLoading ? '加载中…' : libraryError ? '文库未同步' : `${visibleLibrary.length} / ${library.length} 篇论文`}</span></div></>}
          {(view === 'discover' || view === 'library') && <>
            {(view === 'discover' ? loading : libraryLoading) ? <div className="loading-state" role="status"><span className="spinner"/><div><strong>{view === 'discover' ? '正在检索与筛选论文' : '正在加载文库'}</strong><p>等待真实接口返回，完成后自动呈现结果。</p></div></div> : libraryError && view === 'library' ? <div className="error-panel" role="alert"><Icon name="info"/><div><h3>文库加载失败</h3><p>{libraryError}</p><p>暂时无法读取收藏，这不代表文库为空。请检查后端连接后重试。</p><button className="button" onClick={() => void refreshLibrary()}>重新加载文库</button></div></div> : error && view === 'discover' ? <div className="error-panel" role="alert"><Icon name="info"/><div><h3>本次检索未能完成</h3><p>{error}</p><p className="fineprint">真实模式不会使用演示论文填补失败结果。</p><button className="button" onClick={() => setSearchVersion(version => version + 1)}>重试检索</button></div></div> : view === 'library' && library.length > 0 && visibleLibrary.length === 0 ? <EmptyState title="没有符合当前筛选的收藏" icon="search"><p>文库中仍有 {library.length} 篇论文，请清除搜索词或切换阅读状态。</p><button className="button" onClick={() => { setLibrarySearch(''); setLibraryFilter('all') }}>显示全部收藏</button></EmptyState> : visiblePapers.length === 0 ? <EmptyState title={view === 'library' ? '暂无收藏论文' : '没有符合条件的论文'} icon={view === 'library' ? 'bookmark' : 'search'}><p>{view === 'library' ? '在发现页面收藏论文，阅读状态、标签与笔记会保存在本机。' : '尝试扩大日期范围，检查英文检索式，或调整包含与排除关键词。'}</p><button className="button" onClick={() => view === 'library' ? navigate('discover') : (setShowQuery(true), setDays(0))}>{view === 'library' ? '去发现论文' : '调整检索条件'}</button></EmptyState> : <div className="paper-list">{visiblePapers.map(paper => <PaperCard key={paper.id} paper={paper} selected={selected?.id === paper.id} busy={busy === paper.id} onSelect={() => choosePaper(paper)} onSave={() => void updateCollection(paper, { saved: !paper.collection.saved })} onRead={() => void updateCollection(paper, { read: !paper.collection.read })} onIgnore={() => void ignorePaper(paper)} onFeedback={feedback => applyFeedback(paper.id, paper.mode, feedback)} onCompare={() => addComparison(paper)}/>)}</div>}
            {view === 'discover' && !loading && result && <div className="pagination"><button className="button small" disabled={start === 0} onClick={() => setStart(Math.max(0, start - profile.page_size))}>← 上一页</button><span>第 {Math.floor(start / profile.page_size) + 1} 页 · {result.ranking_scope === 'candidate_pool' ? '本轮候选池筛选后分页' : '上游分页后应用个人筛选'}</span><button className="button small" disabled={result.has_more === false || (result.has_more === undefined && start + profile.page_size >= result.total)} onClick={() => setStart(result.next_start ?? start + profile.page_size)}>下一页 →</button></div>}
            <p className="list-footnote"><span/>你的研究判断，始终由你掌握。<span/></p>
          </>}
          {(view === 'detail' || (view === 'local' && selected?.source === 'local')) && (selected ? <>
            {selected.source !== 'local' && <button className="back-button" onClick={() => navigate('discover')}>← 返回发现论文</button>}<article id={selected.source === 'local' ? 'local-paper-detail' : undefined} className="detail-card"><div className="paper-meta"><span className="tag">{selected.source === 'local' ? '本地 PDF' : 'arXiv'} · v{selected.version}</span>{selected.source !== 'local' && <PublicationBadge publication={selected.publication}/>}</div><h2>{selected.title}</h2><p className="detail-authors">{selected.authors.join(' · ') || '作者未核实'}</p><div className="detail-dates">{selected.source === 'local' ? <span>上传时间 <strong>{dateLabel(selected.uploaded_at || '')}</strong></span> : <><span>首次公开 <strong>{dateLabel(selected.published)}</strong></span><span>最近更新 <strong>{dateLabel(selected.updated)}</strong></span></>}<span>编号 <strong className="mono">{selected.id}</strong></span></div><div className="button-row"><a className="button" href={selected.url} target="_blank" rel="noreferrer">{selected.source === 'local' ? '下载原 PDF' : '原文页面'}<Icon name="external" size={14}/></a><a className="button" href={selected.pdf_url} target="_blank" rel="noreferrer">阅读 PDF<Icon name="external" size={14}/></a><button className={`button ${selected.collection.saved ? 'saved' : ''}`} disabled={busy === selected.id} onClick={() => void updateCollection(selected, { saved: !selected.collection.saved })}><Icon name="bookmark" size={14}/>{selected.collection.saved ? '已收藏' : '收藏论文'}</button></div>{selected.source === 'local' ? <LocalAbstractPanel key={selected.id} paper={selected} jobs={jobs} modelReady={!modelUnavailable} busy={busy} onSaved={paper => { replacePaper(paper); setNotice('摘要已核对保存，可开始分析和翻译。已有全文检查点保留。') }} onRun={skill => void runSkill(skill)} onJobAction={(job, action) => void jobAction(job, action)} onSettings={() => navigate('preferences')}/> : <><div className="detail-section"><div className="section-heading"><h3>Abstract</h3><span className="subtle-badge">原文摘要</span></div><p className="full-abstract">{selected.abstract || '本地 PDF 尚未核实摘要、作者与发表日期。标题来自文件名，请使用下方全文工作台阅读与翻译。'}</p><div className="tags">{selected.categories.map(category => <span className="tag neutral" key={category}>{category}</span>)}</div></div><div className="detail-section"><h3>阅读与解读</h3><p className="muted">此处保留摘要操作。全文读取、精读与逐段翻译请使用下方全文研究工作台。</p><div className="button-row"><button className="button primary" disabled={!selected.abstract || modelUnavailable || busy === 'paper_reader'} onClick={() => void runSkill('paper_reader')}><Icon name="sparkle" size={15}/>解读摘要</button><button className="button" disabled={!selected.abstract || modelUnavailable || busy === 'paper_translator'} onClick={() => void runSkill('paper_translator')}><Icon name="translate" size={15}/>翻译摘要</button><button className="button" disabled={busy === selected.id} onClick={() => void updateCollection(selected, { read: !selected.collection.read })}><Icon name="check" size={15}/>{selected.collection.read ? '设为未读' : '标记已读'}</button></div>{modelUnavailable && <p className="notice">{mode === 'demo' ? '演示模式不调用模型。切换真实模式并配置模型后可执行。' : '模型尚未配置。请在“研究偏好”的模型配置中填写 API 地址、模型名称和密钥，并测试连接。'}</p>}</div></>}<ReadingWorkspace key={`${selected.mode}:${selected.id}:v${selected.version}`} paper={selected} jobs={jobs} modelReady={!!health?.model_configured} busy={busy} onRun={(skill, scope, acknowledge, translationJobId) => void runSkill(skill, scope, acknowledge, translationJobId)} onJobAction={(job, action) => void jobAction(job, action)}/><div className="detail-section"><h3>我的研究笔记</h3><button className="button small" disabled={busy === selected.id} onClick={() => void updateCollection(selected, {read: !selected.collection.read})}>{selected.collection.read ? '设为未读' : '标记已读'}</button><label className="field-label">个人标签<input value={tags} onChange={event => setTags(event.target.value)} placeholder="例如：评测基准, 多智能体, 稍后精读"/></label><label className="field-label">阅读笔记<textarea rows={5} value={notes} onChange={event => setNotes(event.target.value)} placeholder="记录与你的课题相关的问题、待验证假设与阅读心得…"/></label><button className="button" disabled={busy === selected.id} onClick={() => void updateCollection(selected, { notes, tags: splitList(tags), saved: true })}>保存笔记与标签</button></div><div className="detail-section"><h3>导出研究资料</h3><div className="button-row"><button className="button" onClick={() => addComparison(selected)}>加入论文对比</button><a className="button" href={`/api/reports/pdf?${new URLSearchParams({paper_id: selected.id, mode})}`} download>研究报告 PDF</a><a className="button" href={exportUrl([selected.id], mode, 'markdown')} download><Icon name="download" size={14}/>Markdown</a><a className="button" href={exportUrl([selected.id], mode, 'bibtex')} download><Icon name="download" size={14}/>BibTeX</a></div><p className="fineprint">{selected.source === 'local' ? '导出包含已保存的原文摘要、笔记和已有精读结果。译文请在双语对照中下载。' : '导出包含来源链接、日期和推荐证据；PDF 纳入已有精读结果，不自动生成未知结论。'}</p></div></article>
          </> : <EmptyState title="先选择一篇论文" icon="book"><p>从发现论文或文库打开论文，查看原文、证据和阅读笔记。</p><button className="button" onClick={() => navigate('discover')}>发现论文</button></EmptyState>)}
          {view === 'skills' && <><section className="section-banner"><h2>论文分析与处理</h2><p>选择一篇论文后运行相应功能，进度和结果可在任务中心查看。</p></section><div className="current-paper"><Icon name="book"/><span>{selected ? selected.title : '尚未选择论文，请先从发现页面选择一篇。'}</span></div><div className="skill-list">{skills.filter(skill => !localContext || ['paper_reader', 'paper_translator', 'fulltext_fetch', 'local_source_fetch', 'chinese_pdf', 'paper_analysis', 'analysis_pdf'].includes(skill.id)).map(skill => <SkillCard key={skill.id} skill={skill} disabled={skill.id !== 'weekly_digest' && (!selected || !skill.available || (mode === 'demo' && (skill.requires_model || ['fulltext_fetch', 'local_source_fetch'].includes(skill.id))))} busy={busy === skill.id} onRun={() => skill.id === 'weekly_digest' ? navigate('subscriptions') : void runSkill(skill.id)}/>)}</div>{!skills.length && <EmptyState title="Skill 注册表暂不可用"><p>请检查后端连接，然后刷新服务状态。</p><button className="button" onClick={() => void refreshConnection()}>刷新连接</button></EmptyState>}<p className="notice">全文读取无需密钥；全文精读和翻译在论文详情中选择范围。已完成段落立即保存，取消或失败后可以恢复。</p></>}
          {view === 'jobs' && <><div className="connection-bar"><span className={`status-dot ${health?.worker?.online ? '' : 'offline'}`}/>{!health ? '后端未连接，正在自动重试连接；任务状态暂时无法确认。' : health.worker?.online ? streamState : 'worker 未连接，请启动独立 worker；已提交任务保留在队列。'}</div>{currentJobs.length ? <div className="job-list">{currentJobs.map(job => <JobCard job={job} key={job.id} busy={busy === job.id} onAction={action => void jobAction(job, action)}/>)}</div> : <EmptyState title="研究任务，从一篇论文开始" icon="jobs"><p>在论文详情或 Skills 工作台发起解读、翻译或推荐解释，任务与结果会持久保存。</p><button className="button" onClick={() => navigate('skills')}>打开 Skills 工作台</button></EmptyState>}<p className="fineprint">断线不影响后台执行；刷新时先读取任务快照，再续接 SSE。模型请求结果不确定时，系统会要求确认后再重试。</p></>}
          {view === 'preferences' && <form className="preferences" onSubmit={saveProfile}><section className="settings-card"><div className="settings-title"><span>01</span><div><h2>你的研究方向</h2><p>规则推荐使用这些主题和关键词。所有偏好均可随时修改。</p></div></div><label className="field-label">研究方向<textarea rows={3} value={profileDraft.interests.join('\n')} onChange={event => setProfileDraft({ ...profileDraft, interests: event.target.value.split('\n') })}/><small>每行一个研究方向；支持中文主题映射与英文关键词。</small></label><div className="field-grid"><label className="field-label">必须包含关键词<input value={profileDraft.include_keywords.join(',')} onChange={event => setProfileDraft({ ...profileDraft, include_keywords: event.target.value.split(',') })} placeholder="例如：agent, evaluation"/><small>多个词用英文逗号分隔。</small></label><label className="field-label">排除关键词<input value={profileDraft.exclude_keywords.join(',')} onChange={event => setProfileDraft({ ...profileDraft, exclude_keywords: event.target.value.split(',') })} placeholder="例如：survey"/></label></div><div className="field-grid"><label className="field-label">默认时间范围<select value={profileDraft.days} onChange={event => setProfileDraft({ ...profileDraft, days: Number(event.target.value) })}><option value={0}>全部时间</option><option value={7}>最近 7 天</option><option value={30}>最近 30 天</option><option value={90}>最近 90 天</option><option value={365}>最近一年</option><option value={730}>近两年（730 天）</option></select><small>始终依据首次公开日期。</small></label><label className="field-label">每页候选数量<select value={profileDraft.page_size} onChange={event => setProfileDraft({ ...profileDraft, page_size: Number(event.target.value) })}><option value={5}>5 篇</option><option value={10}>10 篇</option><option value={20}>20 篇</option></select></label></div><label className="field-label">主题契合权重 · {Math.round((profileDraft.topic_weight ?? 0.75) * 100)}%<input type="range" min="0" max="1" step="0.05" value={profileDraft.topic_weight ?? 0.75} onChange={event => setProfileDraft({ ...profileDraft, topic_weight: Number(event.target.value) })}/><small>其余 {Math.round((1 - (profileDraft.topic_weight ?? 0.75)) * 100)}% 用于标题关键词覆盖。匹配分为排序依据，不代表质量概率。</small></label><button className="button primary" disabled={busy === 'profile'} type="submit"><Icon name="check" size={15}/>{busy === 'profile' ? '保存中…' : '保存研究偏好'}</button></section><section className="settings-card"><div className="settings-title"><span>02</span><div><h2>模型与数据连接</h2><p>密钥仅在后端持久保存；已保存密钥不会回传到页面。</p></div></div><div className="connection-item"><div>arXiv<small>真实公开元数据 · 共享限流与缓存</small></div><span className="status-pill succeeded">已接入</span></div><div className="connection-item"><div>Reader / Translator<small>{health?.model || '尚未设置模型名称'}</small></div><span className={`status-pill ${health?.model_configured ? 'succeeded' : 'queued'}`}>{health?.model_configured ? '已配置' : '未配置'}</span></div><div className="connection-item"><div>语义召回与模型重排<small>阶段一使用可解释的关键词规则排序</small></div><span className="status-pill queued">尚未启用</span></div><div className="connection-item"><div>OpenReview / Semantic Scholar<small>已预留独立适配器，尚未连接数据源</small></div><span className="status-pill queued">后续接入</span></div><ModelSettings onSaved={refreshConnection}/><button type="button" className="button" disabled={busy === 'connection'} onClick={() => void refreshConnection()}><Icon name="refresh" size={15}/>刷新配置状态</button></section></form>}
        </div>
        {!localContext && copilotOpen && <button className="scrim copilot-scrim" aria-label="关闭 Copilot" onClick={() => setCopilotOpen(false)}/>}
        {!localContext && <aside className={`copilot ${copilotOpen ? 'open' : ''}`}><section className="copilot-panel"><div className="copilot-heading"><span className="copilot-mark"><Icon name="sparkle" size={20}/></span><div><h2>Research Copilot</h2><small>摘要分析与翻译</small></div><span className="copilot-status">{health?.model_configured && mode === 'live' ? '模型已配置' : '规则可用'}</span><button className="icon-button copilot-close" aria-label="关闭 Copilot" onClick={() => setCopilotOpen(false)}><Icon name="close" size={16}/></button></div>{selected ? <><div className="selected-paper"><div className="section-eyebrow">当前论文</div><button onClick={() => navigate('detail')}>{selected.title}<Icon name="external" size={12}/></button><p>{selected.source === 'local' ? '本地 PDF' : 'arXiv'} · {dateLabel(selected.published)} · v{selected.version}</p></div><div className="copilot-tabs">{([['evidence','推荐依据'],['reader','摘要解读'],['translator','摘要翻译']] as const).map(([tab,label]) => <button key={tab} className={copilotTab === tab ? 'active' : ''} onClick={() => setCopilotTab(tab)}>{label}</button>)}</div><div className="copilot-content">{copilotTab === 'evidence' ? selected.source === 'local' ? <p className="notice">本地论文尚无核实的摘要评分，请在详情的全文工作台阅读、翻译和下载。</p> : <><SemanticMatch paper={selected} busy={semanticBusy} onRun={() => void runSemantic([selected])} onTasks={() => navigate('jobs')} onSettings={() => navigate('preferences')}/><EvidencePanel paper={selected}/></> : <>{selectedJob?.result != null ? <ResultContent result={selectedJob.result}/> : <div className="model-empty"><span><Icon name={copilotTab === 'translator' ? 'translate' : 'book'} size={24}/></span><h3>{copilotTab === 'translator' ? '忠实于原文的摘要翻译' : '从原文证据理解研究'}</h3><p>{mode === 'demo' ? '演示模式不产生模型输出。' : health?.model_configured ? '提交任务后，由独立 worker 调用模型。结果仅覆盖原文摘要。' : '尚未配置模型。配置后可获取有原文证据的研究解读与翻译。'}</p></div>}{selectedJob && <div className="inline-job"><span>{statusLabel(selectedJob.status)}</span><span>{selectedJob.progress}%</span><p>{selectedJob.error || selectedJob.stage}</p><button onClick={() => navigate('jobs')}>查看任务详情 →</button></div>}<button className="button primary wide" disabled={!selected.abstract || modelUnavailable || busy === (copilotTab === 'translator' ? 'paper_translator' : 'paper_reader')} onClick={() => void runSkill(copilotTab === 'translator' ? 'paper_translator' : 'paper_reader')}>{modelUnavailable ? '模型未配置 / 当前不可用' : copilotTab === 'translator' ? '开始翻译摘要' : '开始解读摘要'}</button>{modelUnavailable && <button className="text-button" onClick={() => navigate('preferences')}>查看模型配置说明 →</button>}</>}</div>{copilotTab === 'evidence' && <div className="copilot-cta"><button className="button wide" disabled={!selected.abstract || busy === 'recommendation_explainer'} onClick={() => void runSkill('recommendation_explainer')}><Icon name="sparkle" size={15}/>生成推荐说明文档<Icon name="arrow" size={15}/></button></div>}</> : <div className="copilot-placeholder"><Icon name="compass" size={32}/><h3>先发现，再深入</h3><p>选择一篇论文，在这里查看研究关联、原文证据与阅读任务。</p><div className="placeholder-lines"><span/><span/><span/></div></div>}<form className="copilot-question" onSubmit={event => { event.preventDefault(); setNaturalQuery(copilotQuestion); void prepareQuery(copilotQuestion, true) }}><input aria-label="向 Copilot 描述研究问题" placeholder="描述研究问题，生成检索式…" value={copilotQuestion} onChange={event => setCopilotQuestion(event.target.value)}/><button aria-label="根据研究问题检索" disabled={queryBusy || !copilotQuestion.trim()}><Icon name="arrow" size={17}/></button></form><p className="copilot-question-note">问题用于关键词映射，不伪装成模型回答。</p></section><section className="flow-panel"><div className="flow-title"><h3><Icon name="node" size={15}/>研究工作流</h3><span className="mono">STAGE 02</span></div>{[['设置研究兴趣', `${profile.interests.length} 个方向`],['真实来源检索', loading ? '检索中' : result ? '已返回' : '待检索'],['规则筛选与证据', result ? '已计算' : '等待数据'],['阅读、翻译与导出', currentJobs.length ? `${currentJobs.length} 个任务` : '按需运行']].map(([label, value], i) => <div className="flow-step" key={label}><span className={i === 0 || (result && i < 3) ? 'done' : ''}>{i === 0 || (result && i < 3) ? '✓' : `0${i + 1}`}</span><p>{label}</p><small>{value}</small></div>)}<p className="fineprint">仅展示可核验的步骤与执行状态，不展示虚构的思考过程。</p></section><section className="research-note"><h3><Icon name="info" size={15}/>保持研究的判断力</h3><p>摘要能帮助你发现相关性，无法替代全文核查。代码、实验与录用信息，在有来源之前保持未知。</p></section></aside>}
        </div>
      </main>
    </div>
    {notice && <div className="toast" role="status"><Icon name="info" size={17}/><span>{notice}</span><button className="icon-button" aria-label="关闭提示" onClick={() => setNotice('')}><Icon name="close" size={16}/></button></div>}
  </div>
}





