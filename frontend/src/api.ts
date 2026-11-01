export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const body: unknown = await response.json()
      if (body && typeof body === 'object' && 'detail' in body) {
        const detail = (body as { detail: unknown }).detail
        message = typeof detail === 'string' ? detail : JSON.stringify(detail)
      }
    } catch { /* Preserve the HTTP status when a proxy returns non-JSON. */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}
export const write = (method: string, data?: unknown): RequestInit => ({ method, body: data === undefined ? undefined : JSON.stringify(data) })
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : '发生未知错误，请重试。'
export const dateLabel = (value: string) => value ? value.slice(0, 10) : '未知'
export const timeLabel = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未知'
export const statusLabel = (value: string) => ({ queued: '等待 worker', running: '执行中', succeeded: '已完成', completed: '已完成', failed: '执行失败', cancelled: '已取消', cancel_requested: '正在取消', needs_confirmation: '结果待确认', partial: '部分完成' }[value] || value)
export const terminalStatus = (value: string) => ['succeeded', 'completed', 'failed', 'cancelled', 'needs_confirmation', 'partial'].includes(value)
export function exportUrl(ids: string[], mode: string, format: string) {
  return `/api/export?${new URLSearchParams({ ids: ids.join(','), mode, format })}`
}
