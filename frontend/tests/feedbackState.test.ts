import assert from 'node:assert/strict'
import test from 'node:test'
import { applyPaperFeedback } from '../src/feedbackState.ts'
import type { Feedback, Paper } from '../src/types.ts'

const feedback = (value: Feedback['value']): Feedback => ({value, reason: '', adjustment: 0})
const hidden = {id: 'one', mode: 'live', collection: {saved: true, read: false, interested: false, tags: ['RL'], notes: 'keep'}, feedback: feedback('too_basic')} as Paper

test('withdrawing ranking feedback preserves an independent hide flag', () => {
  const updated = applyPaperFeedback(hidden, feedback('none'))
  assert.equal(updated.collection.interested, false)
  assert.deepEqual(updated.collection, hidden.collection)
})
test('withdrawing a hide feedback restores visibility', () => {
  const updated = applyPaperFeedback({...hidden, feedback: feedback('not_relevant')}, feedback('none'))
  assert.equal(updated.collection.interested, true)
  assert.equal(updated.collection.saved, true)
  assert.deepEqual(updated.collection.tags, ['RL'])
})
test('explicit ranking feedback restores visibility without changing annotations', () => {
  for (const value of ['relevant', 'too_basic', 'lacks_experiments'] as const) {
    const updated = applyPaperFeedback(hidden, feedback(value))
    assert.equal(updated.collection.interested, true)
    assert.equal(updated.collection.notes, 'keep')
    assert.equal(hidden.collection.interested, false)
  }
})
test('hide feedback leaves the independent collection flag unchanged', () => {
  for (const interested of [false, true]) {
    const updated = applyPaperFeedback({...hidden, collection: {...hidden.collection, interested}}, feedback('not_relevant'))
    assert.equal(updated.collection.interested, interested)
    assert.equal(updated.feedback?.value, 'not_relevant')
  }
})
