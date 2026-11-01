import type { Job } from './types'

export function isOlderJob(incoming: Job, current?: Job): boolean {
  if (!current) return false
  if (incoming.attempts !== current.attempts) return incoming.attempts < current.attempts
  // Backend UTC ISO timestamps retain microseconds; Date.parse would lose that precision.
  return incoming.updated_at < current.updated_at
}

export function mergeJobs(current: Job[], incoming: Job[]): Job[] {
  const previous = new Map(current.map(job => [job.id, job]))
  const merged = new Map(current.map(job => [job.id, job]))
  for (const job of incoming) {
    if (!isOlderJob(job, previous.get(job.id))) merged.set(job.id, job)
  }
  return [...merged.values()].sort((a, b) => b.created_at.localeCompare(a.created_at))
}
