import type { Feedback, Paper } from './types'

export function applyPaperFeedback(paper: Paper, feedback: Feedback): Paper {
  // Match the backend: undo only unhides a paper when undoing a hide feedback.
  const restore = !['none', 'not_relevant'].includes(feedback.value)
    || (feedback.value === 'none' && paper.feedback?.value === 'not_relevant')
  return {...paper, feedback, collection: {...paper.collection, interested: restore ? true : paper.collection.interested}}
}
