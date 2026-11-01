import { FeedbackControl } from './FeedbackControl'
import { TranslationDiagnostics } from './TranslationDiagnostics'
import type { ReactNode } from 'react'
import { PublicationBadge } from './ReadingWorkspace'
import { MatchScore } from './MatchScore'
import type { Feedback, Job, Paper, Skill } from './types'
import { dateLabel, statusLabel, terminalStatus, timeLabel } from './api'

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const shapes: Record<string, ReactNode> = {
    compass: <><circle cx="12" cy="12" r="9"/><path d="m16 8-2.4 5.6L8 16l2.4-5.6Z"/></>,
    book: <><path d="M3 4h6a4 4 0 0 1 3 1 4 4 0 0 1 3-1h6v15h-6a4 4 0 0 0-3 1 4 4 0 0 0-3-1H3Z"/><path d="M12 5v15"/></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M17.5 14v7M14 17.5h7"/></>,
    jobs: <><path d="M8 3h8v4H8zM8 5H5v16h14V5h-3M8 12h8M8 16h5"/></>,
    settings: <><path d="M3 7h8m4 0h6M3 17h4m4 0h10M11 4v6M7 14v6"/></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>,
    external: <path d="M14 3h7v7m0-7L10 14M10 4H4v16h16v-6"/>,
    bookmark: <path d="M6 3h12v18l-6-4-6 4Z"/>,
    check: <path d="m5 12 4 4L19 6"/>,
    close: <path d="m6 6 12 12M6 18 18 6"/>,
    download: <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>,
    sparkle: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/><path d="M20 2v4m-2-2h4"/></>,
    translate: <><path d="M3 5h11M8 2v3m4 0c-1 5-4 8-9 10m2-7c2 4 4 5 8 7m0 6 5-11 5 11m-8-4h6"/></>,
    refresh: <><path d="M20 5v5h-5M4 19v-5h5"/><path d="M5 8a8 8 0 0 1 13-3l2 5M4 14l2 5a8 8 0 0 0 13-3"/></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
    node: <><circle cx="5" cy="12" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="18" cy="19" r="2"/><path d="m7 11 9-5M7 13l9 5"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></>,
    eye: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></>,
    filter: <path d="M3 4h18l-7 8v7l-4 2v-9Z"/>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shapes[name] || shapes.info}</svg>
}

export function EmptyState({ title, children, icon = 'search' }: { title: string; children: ReactNode; icon?: string }) {
  return <div className="empty-state"><span className="empty-icon"><Icon name={icon} size={28}/></span><h3>{title}</h3><div>{children}</div></div>
}

export function PaperCard({ paper, selected, busy, onSelect, onSave, onRead, onIgnore, onCompare, onFeedback }: {
  paper: Paper; selected: boolean; busy: boolean; onSelect: () => void; onSave: () => void; onRead: () => void; onIgnore: () => void; onCompare: () => void; onFeedback: (feedback: Feedback) => void
}) {
  const rec = paper.recommendation
  const local = paper.source === 'local'
  return <article className={`paper-card ${selected ? 'selected' : ''}`}>
    <div className="paper-meta"><span className="tag">{paper.categories[0] || paper.source}</span><span className="tag neutral">{paper.source === 'local' ? '本地导入' : '仅摘要'}</span><span className="paper-date">{local ? '上传于 ' : ''}{dateLabel(local ? paper.uploaded_at || '' : paper.published)}</span>{!local && <span className="match"><b>{paper.semantic?.result ? paper.semantic.result.score : rec ? Math.round(rec.score) : '—'}</b><small>{paper.semantic?.result ? '语义分' : rec ? '关键词分' : '未评分'}</small></span>}</div>
    <button className="paper-title" onClick={onSelect}>{paper.title}</button>
    {!local && <div className="publication-row"><PublicationBadge publication={paper.publication}/></div>}<p className="authors">{paper.authors.slice(0, 3).join(' · ')}{paper.authors.length > 3 ? ` 等 ${paper.authors.length} 位作者` : ''}</p>
    <p className="abstract-preview">{paper.abstract}</p>
    {!local && <div className="paper-reason"><Icon name="sparkle" size={14}/><span>{rec?.interests.length ? `关联研究：${rec.interests.join(' / ')}` : rec ? '当前研究兴趣暂无直接关键词命中，请结合摘要判断。' : '暂无推荐记录，请先完成检索与排序。'}</span></div>}
    <div className="card-footer"><a href={paper.url} target="_blank" rel="noreferrer">{paper.source === 'local' ? '本地 PDF' : 'arXiv'} · v{paper.version}<Icon name="external" size={12}/></a><div className="card-actions"><button onClick={onCompare}>加入对比</button>{!local && <button onClick={onIgnore} disabled={busy} title="标记不相关并隐藏，可在推荐反馈中撤回">不感兴趣</button>}<button className={paper.collection.read ? 'mint' : ''} onClick={onRead} disabled={busy}><Icon name={paper.collection.read ? 'check' : 'eye'} size={14}/>{paper.collection.read ? '已读' : '标记已读'}</button><button className={paper.collection.saved ? 'lime' : ''} onClick={onSave} disabled={busy}><Icon name="bookmark" size={14}/>{paper.collection.saved ? '已收藏' : '收藏'}</button></div></div>
    {!local && <FeedbackControl paper={paper} onChange={onFeedback}/>}
  </article>
}

export function EvidencePanel({ paper }: { paper: Paper }) {
  const rec = paper.recommendation
  if (!rec) return <div className="evidence-content"><h3>暂无推荐记录</h3><p className="muted">该论文尚未完成推荐评分。请在发现页面检索论文后查看原文证据与匹配依据。</p><p className="fineprint">未评分不代表论文不相关或质量不足。</p></div>
  return <div className="evidence-content">
    <MatchScore recommendation={rec}/>
    <div className="recommend-box"><h4><Icon name="node" size={14}/>命中的研究兴趣</h4>{rec.interests.length ? <ul className="matched-interests">{rec.interests.map((interest, index) => <li key={`${interest}-${index}`}>{interest}</li>)}</ul> : <p>未发现直接匹配，保留为探索候选。</p>}</div>
    <div className="section-eyebrow">可核验的原文证据</div>
    {rec.evidence.length ? rec.evidence.map((evidence, i) => <blockquote key={i}><p>{evidence.text}</p><cite>{evidence.location || '摘要'} · <a href={paper.url} target="_blank" rel="noreferrer">查看来源 ↗</a></cite></blockquote>) : <p className="muted">暂无命中的摘要句子，不作额外推断。</p>}
    <details className="unknowns"><summary>待核实信息 · {rec.unknowns.length} 项</summary><ul>{rec.unknowns.map((unknown, i) => <li key={i}>{unknown}</li>)}</ul></details>
    <p className="fineprint">分析覆盖：摘要 · {rec.method}<br/>实验、代码、数据与会议录用情况须查阅原文核实。</p>
  </div>
}

export function JobCard({ job, busy, onAction }: { job: Job; busy: boolean; onAction: (action: 'cancel' | 'retry') => void }) {
  const needsConfirmation = job.status === 'needs_confirmation' || job.requires_confirmation === true
  return <article className="job-card"><div className="job-heading"><span className="skill-icon"><Icon name={job.skill_id === 'paper_translator' ? 'translate' : 'sparkle'}/></span><div><h3>{job.skill_id === 'paper_analysis' ? '论文总体分析' : job.skill_id === 'analysis_pdf' ? '总体分析中文排版' : job.skill_id === 'weekly_digest' ? '主题订阅周报' : job.skill_id === 'chinese_pdf' ? '中文论文排版' : job.skill_id === 'glossary_extractor' ? '论文术语提取' : job.skill_id === 'semantic_ranker' ? '研究兴趣语义匹配' : job.skill_id === 'local_source_fetch' ? '恢复原图与源文件' : job.skill_id === 'fulltext_fetch' ? '全文读取' : job.skill_id === 'paper_translator' ? (job.scope === 'fulltext' ? '全文翻译' : '摘要翻译') : job.skill_id === 'paper_reader' ? (job.scope === 'fulltext' ? '全文结构化精读' : '论文摘要解读') : '推荐依据说明'}</h3><p className="mono">{job.skill_id === 'weekly_digest' ? '真实来源 · 主题汇总' : job.paper_id}</p></div><span className={`status-pill ${job.status}`}>{statusLabel(job.status)}</span></div><div className="job-progress"><i style={{ width: `${job.progress}%` }}/></div><div className="job-caption"><span>{job.stage} · {job.progress}%</span><span>尝试 {job.attempts} 次</span></div>{job.error && <p className="error-text">{job.error}</p>}<TranslationDiagnostics job={job}/>{needsConfirmation && <p className="notice">模型请求可能已完成。为避免重复计费，需确认后再重试；结果不会被伪造为成功。</p>}<div className="job-footer"><time>{timeLabel(job.updated_at || job.created_at)}</time><div className="button-row">{!terminalStatus(job.status) && <button className="button small" disabled={busy} onClick={() => onAction('cancel')}>取消任务</button>}{['failed', 'partial', 'cancelled', 'needs_confirmation'].includes(job.status) && <button className="button small" disabled={busy || !(job.can_retry ?? (job.attempts < 3))} onClick={() => onAction('retry')}>{needsConfirmation ? '确认计费风险并重试' : '重试未完成部分'}</button>}{job.status === 'succeeded' && <a className="button small" href={`/api/jobs/${job.id}/artifact`} download><Icon name="download" size={13}/>下载产物</a>}</div></div>{job.result != null && <details className="job-result"><summary>查看运行结果</summary><ResultContent result={job.result}/></details>}</article>
}
export function ResultContent({ result }: { result: unknown }) {
  const markdown = typeof result === 'object' && result !== null && 'markdown' in result ? String((result as {markdown: unknown}).markdown) : JSON.stringify(result, null, 2)
  return <pre className="result-content">{markdown}</pre>
}
export function SkillCard({ skill, disabled, busy, onRun }: { skill: Skill; disabled: boolean; busy: boolean; onRun: () => void }) {
  return <article className="skill-card"><div className="skill-top"><span className="skill-icon"><Icon name={skill.id === 'paper_translator' ? 'translate' : skill.id === 'paper_reader' ? 'book' : 'sparkle'} size={22}/></span><div><h3>{skill.name}</h3><span className="mono">{skill.id} · v{skill.version}</span></div></div><p>{skill.description}</p><div className="skill-bottom"><span className={`status-pill ${skill.available ? 'succeeded' : 'queued'}`}>{skill.available ? (skill.requires_model ? '模型已配置' : '规则执行 · 可用') : '模型未配置'}</span><button className="button" onClick={onRun} disabled={disabled || busy}>{busy ? '提交中…' : skill.id === 'weekly_digest' ? '配置主题订阅' : '对当前论文运行'}<Icon name="arrow" size={14}/></button></div><details className="schema-details"><summary>输入 / 输出规范</summary><pre>{JSON.stringify({ input: skill.input_schema, output: skill.output_schema }, null, 2)}</pre></details></article>
}



