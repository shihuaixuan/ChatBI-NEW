import assert from 'node:assert/strict'
import test from 'node:test'

import { projectResearchReportAnswer } from './researchReportProjection.ts'

test('Research 部分报告投影为可读文本', () => {
  const answer = projectResearchReportAnswer(
    JSON.stringify({
      kind: 'partial',
      summary: '研究未完整收口。',
      confirmed: [{ purpose: '验证总GMV变化' }],
      unconfirmed: ['主要原因尚未确认'],
      recommendation: '补充证据后重试。',
    })
  )

  assert.match(answer, /研究未完整收口/)
  assert.match(answer, /已完成的验证：\n- 验证总GMV变化/)
  assert.match(answer, /尚未确认：\n- 主要原因尚未确认/)
  assert.doesNotMatch(answer, /"confirmed"/)
})

test('普通回答不进行 JSON 投影', () => {
  assert.equal(projectResearchReportAnswer('总GMV下降。'), '总GMV下降。')
  assert.equal(projectResearchReportAnswer('{"value":1}'), '{"value":1}')
})

