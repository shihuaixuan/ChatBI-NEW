import * as assert from 'node:assert/strict'

import {
  buildGraphInteractionResponse,
  buildGraphWorkflowSteps,
  graphProgressHeadline,
  normalizeGraphInteraction,
} from '../src/views/chat/execution-component/graphWorkflowDisplay'

const waitingInteraction = {
  interaction_id: 'interaction-1',
  run_id: 'run-1',
  node_name: 'ask_metric_selection',
  status: 'pending',
  prompt: '请选择要分析的指标。',
  response_schema: {
    type: 'object',
    properties: {
      metric: { type: 'string' },
      skipped: { type: 'boolean' },
    },
  },
  options: [
    { label: '访问人数', value: 11 },
    { label: '访客数', value: 12 },
  ],
}

const waitingTrace = {
  run_id: 'run-1',
  status: 'waiting_input',
  current_node: 'ask_metric_selection',
  nodes: [
    { name: 'classify_question', status: 'succeeded', output: { category: 'data' } },
    {
      name: 'retrieve_knowledge',
      status: 'succeeded',
      output: { selected_assets: { metrics: [{ name: '访问人数' }] } },
    },
    { name: 'ask_metric_selection', status: 'waiting_input', output: { prompt: '请选择要分析的指标。' } },
  ],
}

const succeededTrace = {
  run_id: 'run-2',
  status: 'succeeded',
  current_node: 'finish',
  nodes: [
    { name: 'classify_question', status: 'succeeded', output: { category: 'data' } },
    { name: 'generate_sql', status: 'succeeded', output: { sql: 'select 1' } },
    { name: 'execute_sql', status: 'succeeded', output: { row_count: 1 } },
    { name: 'compose_final_reply', status: 'succeeded', output: { answer: '今天访问人数为 100。' } },
  ],
}

const waitingSteps = buildGraphWorkflowSteps(waitingTrace, waitingInteraction)
assert.equal(waitingSteps.at(-1)?.label, '选择分析指标')
assert.equal(waitingSteps.at(-1)?.status, 'waiting_input')
assert.equal(waitingSteps.at(-1)?.summary, '请选择要分析的指标。')
assert.equal(graphProgressHeadline(waitingTrace, waitingInteraction), '需要你补充信息：请选择要分析的指标。')

const normalized = normalizeGraphInteraction(waitingInteraction)
assert.equal(normalized.title, '请选择要分析的指标')
assert.equal(normalized.targetSlots[0], 'metric')
assert.deepEqual(buildGraphInteractionResponse(waitingInteraction, normalized.options[0]), {
  metric: '11',
})
assert.deepEqual(buildGraphInteractionResponse(waitingInteraction, undefined, '客流人数'), {
  metric: '客流人数',
})
assert.deepEqual(buildGraphInteractionResponse(waitingInteraction, undefined, '', true), {
  skipped: true,
})

const doneSteps = buildGraphWorkflowSteps(succeededTrace, null)
assert.equal(graphProgressHeadline(succeededTrace, null), '已完成')
assert.equal(doneSteps.at(-1)?.label, '整理回复')
assert.equal(doneSteps.at(-1)?.status, 'succeeded')
assert.equal(doneSteps.find((step) => step.key === 'execute_sql')?.summary, '返回 1 行')

console.info('graphWorkflowDisplay tests passed')
