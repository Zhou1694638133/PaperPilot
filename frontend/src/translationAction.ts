export interface TranslationActionInput {
  loading: boolean; hasDocument: boolean; modelReady: boolean; live: boolean
  requiresReview: boolean; acknowledged: boolean; active: boolean; submitting: boolean
  complete: boolean; status?: string; currentAlignment: boolean; canRetry?: boolean
}

export function translationAction(input: TranslationActionInput) {
  if (input.complete) return { action: 'view', label: '查看全文译文', reason: '', disabled: false } as const
  const retry = input.currentAlignment && ['failed', 'partial', 'cancelled', 'needs_confirmation'].includes(input.status || '')
  const reason = input.loading ? '正在核对全文状态，请稍候。'
    : !input.live ? '请切换到真实模式后翻译。'
    : !input.hasDocument ? '请先读取全文；全文获取完成不代表翻译完成。'
    : !input.modelReady ? '请先在研究偏好中配置大模型。'
    : input.requiresReview && !input.acknowledged ? '请核对下方解析警告，并勾选确认后开始全文翻译。'
    : input.active ? '已有全文任务排队或执行中，请等待完成或取消该任务。'
    : input.submitting ? '正在提交全文翻译任务…'
    : retry && input.canRetry === false ? '当前任务暂不可恢复，请查看任务提示。' : ''
  return { action: retry ? 'retry' : 'start', label: retry ? '继续全文翻译（未完成部分）' : '开始全文翻译', reason, disabled: !!reason } as const
}
