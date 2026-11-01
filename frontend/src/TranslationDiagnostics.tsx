import { useEffect, useState } from 'react'
import { api, errorMessage } from './api'
import type { Job, Mode, TranslationPreflight } from './types'

function Report({ report }: { report: TranslationPreflight }) {
  return <details className="parse-warnings"><summary>整篇翻译预检 · {report.total} 块</summary><p>需要翻译 {report.counts.prose || 0} · 标识符保留 {report.counts.identifier || 0} · 结构保留 {report.counts.protected || 0} · 需核对 {report.counts.blocked || 0}</p><p>{report.note}</p>{report.items.filter(item => ['blocked', 'identifier'].includes(item.kind)).map(item => <p key={item.paragraph_id}><strong>{item.paragraph_id}</strong> · {item.location}<br/>{item.reason}</p>)}</details>
}

export function TranslationDiagnostics({ job }: { job: Job }) {
  const state = job.translation_state
  if (!state) return null
  return <div>{state.preflight && <Report report={state.preflight}/>} {!!state.failures.length && <details className="parse-warnings" open><summary>未完成块 · {state.failures.length} 项</summary><p>成功检查点已保留。重试只处理未完成块；预检提示需先核对原文，重试不会猜补内容。</p>{state.failures.map(item => <p key={item.paragraph_id}><strong>{item.paragraph_id}</strong> · {item.location}<br/>{item.error || item.reason}</p>)}</details>}</div>
}

export function TranslationPrecheck({ paperId, mode, ready }: {paperId: string; mode: Mode; ready: boolean}) {
  const [report, setReport] = useState<TranslationPreflight | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    setReport(null); setError('')
    if (!ready) return
    const controller = new AbortController()
    void api<TranslationPreflight>(`/translation/preflight?${new URLSearchParams({paper_id:paperId, mode})}`, {signal:controller.signal})
      .then(value => { if (!controller.signal.aborted) setReport(value) }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [paperId, mode, ready, refresh])
  if (!ready) return null
  return <section aria-label="翻译预检">{report ? <Report report={report}/> : <p role="status">{error || '正在执行整篇预检，不调用模型…'}</p>}<button className="button small" onClick={() => setRefresh(value => value + 1)}>重新预检</button></section>
}
