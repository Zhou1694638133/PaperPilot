import type { Paper } from './types'
import { statusLabel, terminalStatus } from './api'

export function SemanticMatch({ paper, busy, onRun, onTasks, onSettings }: {
  paper: Paper; busy: boolean; onRun: () => void; onTasks: () => void; onSettings: () => void
}) {
  const state = paper.semantic
  const result = state?.result
  const job = state?.job
  const active = !!job && !terminalStatus(job.status)
  return <section className="semantic-match" aria-label="大模型语义匹配">
    <div className="semantic-heading"><h3>大模型语义匹配</h3><span className="tag">仅摘要</span></div>
    {result ? <>
      <div className="semantic-comparison"><div><span>语义匹配分</span><strong>{result.score}<small> / 100</small></strong></div><div><span>关键词对照分</span><strong>{paper.recommendation?.score ?? '—'}<small> / 100</small></strong></div></div>
      <p className="fineprint">模型：{result.model} · 结果已保存。两个分数独立展示，用于相关性排序，不代表论文质量。</p>
      <details className="semantic-formula"><summary>语义分的计算方式</summary><p>最相关兴趣得分 {result.best_interest_score} × 70% ＋ 全部兴趣平均分 {result.mean_interest_score} × 30% = {result.score}。各项展示值取整精度可能导致微小差异。</p><p>侧重最相关方向，同时考虑兴趣覆盖。0 为无证据关联，1–29 为背景提及，30–59 为间接关联，60–79 为直接关联，80–100 为核心研究方向。模型判断仍需核对。</p></details>
      <h4>逐项推荐理由</h4>
      {[...result.interests].sort((a, b) => b.score - a.score).map(item => <details className="semantic-interest" key={item.interest}>
        <summary><span>{item.interest}</span><b>{item.score} 分</b></summary><p>{item.reason}</p>
        {item.evidence.length ? item.evidence.map((evidence, index) => <blockquote key={index}><p>{evidence.text}</p><cite>{evidence.location} · <a href={paper.url} target="_blank" rel="noreferrer">查看原文 ↗</a></cite></blockquote>) : <p className="fineprint">摘要中未找到支持该兴趣关联的证据。</p>}
      </details>)}
      <p className="semantic-lead">{[...result.interests].sort((a, b) => b.score - a.score)[0]?.reason}</p>
      <details className="unknowns"><summary>语义分析边界与未知项</summary><ul>{result.unknowns.map((item, index) => <li key={index}>{item}</li>)}</ul></details>
    </> : <>
      <p className="fineprint">{state?.message || '正在读取语义评分状态，当前使用关键词评分。'}</p>
      {job && <div className="inline-job" role="status"><span>{statusLabel(job.status)}</span><span>{job.progress}%</span><p>{job.error || job.stage}</p><button onClick={onTasks}>查看任务 / 取消 / 重试 →</button></div>}
      {state?.status === 'not_started' && <><button className="button primary wide" disabled={busy} onClick={onRun}>{busy ? '正在提交…' : '评估当前论文语义匹配'}</button><p className="fineprint">使用已保存的全部研究兴趣和此论文摘要，调用已配置模型；相同输入复用结果。</p></>}
      {state?.status === 'unavailable' && <button className="button wide" onClick={onSettings}>查看研究偏好与模型配置</button>}
      {!state && <button className="button wide" disabled={busy} onClick={onRun}>读取 / 评估语义匹配</button>}
      {active && <p className="fineprint">任务由独立 worker 执行，进度通过 SSE 推送；期间保留关键词分。</p>}
    </>}
  </section>
}
