import assert from 'node:assert/strict'
import test from 'node:test'
import { isOlderJob, mergeJobs } from '../src/jobState.ts'

const failed = { id: 'one', attempts: 1, status: 'failed', error: 'old failure', created_at: '2026-10-26T10:00:00.000001+00:00', updated_at: '2026-10-26T10:01:00.000001+00:00' }
const queued = { ...failed, status: 'queued', error: null, updated_at: '2026-10-26T10:02:00.000001+00:00' }
const running = { ...queued, attempts: 2, status: 'running', updated_at: '2026-10-26T10:03:00.000001+00:00' }
test('replayed failure cannot overwrite retry or close its stream', () => {
  assert.equal(isOlderJob(failed, queued), true)
  assert.equal(isOlderJob(failed, running), true)
  assert.deepEqual(mergeJobs([running], [failed]), [running])
})
test('snapshot recovers retry from another tab', () => {
  assert.deepEqual(mergeJobs([failed], [running]), [running])
})
test('microsecond order prevents delayed HTTP snapshot rollback', () => {
  const next = { ...running, updated_at: '2026-10-26T10:03:00.000002+00:00' }
  assert.deepEqual(mergeJobs([next], [running]), [next])
})
test('new terminal result replaces active state and preserves other jobs', () => {
  const done = { ...running, status: 'succeeded', updated_at: '2026-10-26T10:04:00.000001+00:00' }
  const other = { ...failed, id: 'two' }
  const merged = mergeJobs([running, other], [done])
  assert.equal(merged.find(job => job.id === 'one')?.status, 'succeeded')
  assert.equal(merged.length, 2)
})
