import { useEffect, useRef, useState } from 'react'
import { api, errorMessage, write } from './api'
import type { Feedback, Paper } from './types'

export const feedbackLabels: Record<Feedback['value'], string> = {
  none: '尚未反馈', relevant: '与研究相关', not_relevant: '不相关 · 隐藏', too_basic: '内容过于基础', lacks_experiments: '希望更多实验依据',
}
export function FeedbackControl({ paper, onChange }: { paper: Paper; onChange?: (feedback: Feedback) => void }) {
  const [saved, setSaved] = useState<Feedback>(paper.feedback ?? { value: 'none', reason: '', adjustment: 0 })
  const [value, setValue] = useState<Feedback['value']>(saved.value)
  const [reason, setReason] = useState(saved.reason)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const pending = useRef(false)
  useEffect(() => {
    const incoming = paper.feedback ?? { value: 'none' as const, reason: '', adjustment: 0 }
    setSaved(incoming); setValue(incoming.value); setReason(incoming.reason)
  }, [paper.id, paper.mode, paper.feedback])
  async function save(next: Feedback['value']) {
    if (pending.current) return
    pending.current = true
    setBusy(true); setMessage('')
    try {
      const result = await api<Feedback>('/feedback/' + encodeURIComponent(paper.id) + '?mode=' + paper.mode, write('PUT', { value: next, reason: next === 'none' ? '' : reason }))
      setSaved(result); setValue(result.value); setReason(result.reason)
      setMessage(next === 'none' ? '已撤回反馈。' : '反馈已保存；发现页排序/隐藏已更新，后续周报会应用。')
      onChange?.(result)
    } catch (cause) { setMessage(errorMessage(cause)) }
    finally { pending.current = false; setBusy(false) }
  }
  if (paper.source === 'local') return null
  return <details className="feedback-control"><summary>推荐反馈 · {feedbackLabels[saved.value]}{saved.adjustment !== 0 && ('（排序 ' + (saved.adjustment > 0 ? '+' : '') + saved.adjustment + '）')}</summary>
    <p className="fineprint">个人判断只影响这篇论文的排序或隐藏；保留原始关键词／语义分，不作为实验质量事实。</p>
    <label className="field-label">反馈类型<select value={value} onChange={e => setValue(e.target.value as Feedback['value'])}>{Object.entries(feedbackLabels).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    <label className="field-label">原因（可选）<input maxLength={1000} value={reason} onChange={e => setReason(e.target.value)} placeholder="例如：关注多轮工具调用中的信用分配"/></label>
    <div className="button-row"><button className="button small" disabled={busy || value === 'none'} onClick={() => void save(value)}>{busy ? '保存中…' : '保存反馈'}</button><button className="button small" disabled={busy || saved.value === 'none'} onClick={() => void save('none')}>撤回反馈</button></div>
    {message && <p role="status">{message}</p>}
  </details>
}

