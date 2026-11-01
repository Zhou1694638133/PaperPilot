import assert from 'node:assert/strict'
import test from 'node:test'
import { translationAction } from '../src/translationAction.ts'

const ready = { loading: false, hasDocument: true, modelReady: true, live: true, requiresReview: false, acknowledged: false, active: false, submitting: false, complete: false, currentAlignment: false }

test('PDF review must be acknowledged before fulltext translation', () => {
  const blocked = translationAction({...ready, requiresReview: true})
  assert.equal(blocked.disabled, true)
  assert.match(blocked.reason, /解析警告/)
  const accepted = translationAction({...ready, requiresReview: true, acknowledged: true})
  assert.equal(accepted.disabled, false)
  assert.equal(accepted.action, 'start')
  assert.equal(accepted.label, '开始全文翻译')
})
test('unconfigured model and missing fulltext have explicit reasons', () => {
  assert.match(translationAction({...ready, modelReady:false}).reason, /配置大模型/)
  assert.match(translationAction({...ready, hasDocument:false}).reason, /获取完成不代表翻译完成/)
  assert.equal(translationAction({...ready, loading:true}).disabled, true)
  assert.equal(translationAction({...ready, live:false}).disabled, true)
})
test('completed saved translation can be viewed without model or review', () => {
  const action=translationAction({...ready, complete:true, modelReady:false, requiresReview:true})
  assert.equal(action.action, 'view')
  assert.equal(action.disabled, false)
})
test('partial and failed current translations resume existing job', () => {
  for (const status of ['partial','failed','cancelled','needs_confirmation']) {
    assert.equal(translationAction({...ready,status,currentAlignment:true}).action,'retry')
  }
  assert.equal(translationAction({...ready,status:'partial',currentAlignment:true,canRetry:false}).disabled,true)
})
test('stale document translation is not resumed and concurrent work is blocked', () => {
  assert.equal(translationAction({...ready,status:'partial',currentAlignment:false}).action,'start')
  assert.equal(translationAction({...ready,active:true}).disabled,true)
  assert.equal(translationAction({...ready,submitting:true}).disabled,true)
})
