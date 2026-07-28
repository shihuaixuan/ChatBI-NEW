import assert from 'node:assert/strict'
import test from 'node:test'

import { buildAgentFlow } from './agentTimelineProjection.ts'
import {
  normalizeAgentEvent,
  reduceAgentEvent,
} from '../answer/agentEventReducer.ts'

const CONTRACT = {
  'question.understood': { kind: 'thinking', phase: 'end' },
  'step.started': { kind: 'run', phase: 'start' },
  'reasoning.snapshot': { kind: 'thinking', phase: 'snapshot' },
  'tool.called': { kind: 'tool', phase: 'start' },
  'tool.completed': { kind: 'tool', phase: 'end' },
  'tool.failed': { kind: 'tool', phase: 'error' },
  'workflow.step': { kind: 'tool', phase: 'start' },
  'clarification.required': { kind: 'interaction', phase: 'start' },
  'clarification.accepted': { kind: 'interaction', phase: 'end' },
  'run.failed': { kind: 'run', phase: 'error' },
}

function agentEvent(domain, payload = {}) {
  return { ...CONTRACT[domain], domain, ...payload }
}

test('结构化事件契约产生回答投影', () => {
  const currentRecord = { execution_events: [] }

  reduceAgentEvent(currentRecord, {
    kind: 'text',
    phase: 'end',
    domain: 'answer.completed',
    content: { record_id: 7, content: '完成' },
  })

  assert.equal(currentRecord.sql_answer, '完成')
  assert.equal(currentRecord.chart_answer, '完成')
  assert.equal(currentRecord.execution_events[0].domain, 'answer.completed')
})

test('未知 domain 不影响事件消费', () => {
  const event = normalizeAgentEvent({
    kind: 'extension',
    phase: 'snapshot',
    domain: 'custom.domain',
    content: { value: 1 },
  })

  assert.equal(event.domain, 'custom.domain')
  assert.equal(event.value, 1)
})

test('完成的运行保留中间失败步骤，但整体展示为已完成并标记重试', () => {
  const flow = buildAgentFlow({
    record_id: 715,
    run_id: 2,
    status: 'finished',
    steps: [
      { id: 11, index: 1, status: 'success' },
      { id: 12, index: 2, status: 'success' },
      { id: 13, index: 3, status: 'success' },
    ],
    tool_calls: [
      {
        tool_call_id: 'compile-1',
        step_id: 11,
        tool_name: 'compile_semantic_sql',
        status: 'succeeded',
        result_summary: { sql: 'select 1' },
      },
      {
        tool_call_id: 'execute-1',
        step_id: 12,
        tool_name: 'execute_sql',
        status: 'failed',
        error_code: 'invalid_date',
      },
      {
        tool_call_id: 'execute-2',
        step_id: 13,
        tool_name: 'execute_sql',
        status: 'succeeded',
        result_summary: { row_count: 1, fields: ['customer_count'] },
      },
    ],
    events: [],
  })

  assert.equal(flow.status, 'success')
  assert.equal(flow.failedCount, 1)
  assert.match(flow.headline, /期间重试 1 次/)
  assert.equal(flow.steps[1].title, '执行查询')
  assert.equal(flow.steps[1].status, 'failed')
})

test('同一推理轮的多个 Tool Call 分别展示且失败互不覆盖', () => {
  const flow = buildAgentFlow(undefined, [
    agentEvent('step.started', { sequence: 1, step_id: 21, step_index: 1 }),
    agentEvent('tool.called', {
      sequence: 2,
      step_id: 21,
      tool_call_id: 'call-a',
      tool_name: 'search_terminology',
      args_summary: { query: 'GMV' },
    }),
    agentEvent('tool.called', {
      sequence: 3,
      step_id: 21,
      tool_call_id: 'call-b',
      tool_name: 'get_sql_examples',
      args_summary: { query: 'GMV' },
    }),
    agentEvent('tool.failed', {
      sequence: 4,
      step_id: 21,
      tool_call_id: 'call-a',
      tool_name: 'search_terminology',
      status: 'failed',
      error_code: 'search_failed',
    }),
    agentEvent('tool.completed', {
      sequence: 5,
      step_id: 21,
      tool_call_id: 'call-b',
      tool_name: 'get_sql_examples',
      status: 'succeeded',
      result_summary: { count: 2 },
    }),
  ])

  const tools = flow.steps.filter((step) => step.kind === 'tool')
  assert.equal(tools.length, 2)
  assert.deepEqual(
    tools.map((step) => [step.toolCallId, step.status]),
    [
      ['call-a', 'failed'],
      ['call-b', 'success'],
    ]
  )
  assert.equal(tools[1].result.count, 2)
})

test('按 sequence 补拉相同事件不会重复写入记录', () => {
  const currentRecord = { execution_events: [] }
  const event = {
    kind: 'tool',
    phase: 'start',
    domain: 'tool.called',
    run_id: 8,
    sequence: 3,
    content: { tool_call_id: 'call-1', tool_name: 'execute_sql' },
  }

  reduceAgentEvent(currentRecord, event)
  reduceAgentEvent(currentRecord, event)

  assert.equal(currentRecord.execution_events.length, 1)
})

test('流式事件能在工具结果返回前展示正在执行的节点', () => {
  const flow = buildAgentFlow(
    undefined,
    [
      {
        ...agentEvent('question.understood', { sequence: 1 }),
        rewritten_question: '今天店铺的客户数',
        intent_type: 'metric_query',
        validation: { status: 'valid' },
      },
      agentEvent('step.started', { sequence: 2, step_index: 1 }),
      {
        ...agentEvent('reasoning.snapshot', { sequence: 3 }),
        content: '需要先检索客户数对应的指标口径，再决定查询字段。',
      },
      {
        ...agentEvent('tool.called', { sequence: 4 }),
        tool_name: 'search_semantic_assets',
        args_summary: {
          rewritten_question: '今天店铺的客户数',
          intent: { metric_mentions: ['客户数'], time_mentions: ['今天'] },
        },
      },
    ],
    true
  )

  assert.equal(flow.status, 'running')
  assert.equal(flow.steps[0].title, '理解问题')
  assert.equal(flow.steps[1].title, '思考下一步')
  assert.equal(flow.steps[1].status, 'success')
  assert.match(flow.steps[1].thinking, /检索客户数对应的指标口径/)
  assert.equal(flow.steps[2].title, '检索语义资产')
  assert.equal(flow.steps[2].status, 'running')
  assert.equal(flow.steps[2].args.rewritten_question, '今天店铺的客户数')
  assert.deepEqual(flow.steps[2].args.intent.metric_mentions, ['客户数'])
  assert.match(flow.headline, /检索语义资产/)
})

test('失败终止事件会覆盖轮询到的旧运行状态', () => {
  const flow = buildAgentFlow(
    {
      record_id: 716,
      run_id: 3,
      status: 'running',
      steps: [{ index: 1, tool_name: 'execute_sql', status: 'running' }],
      events: [],
    },
    [
      agentEvent('step.started', { sequence: 1, step_index: 1 }),
      agentEvent('tool.called', { sequence: 2, tool_name: 'execute_sql' }),
      agentEvent('run.failed', { sequence: 3, content: '已达最大步数 12' }),
    ],
    false
  )

  assert.equal(flow.status, 'failed')
  assert.equal(flow.steps.find((step) => step.toolName === 'execute_sql')?.status, 'failed')
  assert.match(flow.headline, /执行失败/)
})

test('前置澄清按工作流节点展示而不是模型工具调用', () => {
  const flow = buildAgentFlow(undefined, [
    {
      ...agentEvent('question.understood', { sequence: 1 }),
      rewritten_question: '今天店铺的客户数',
      validation: { status: 'clarification_required' },
    },
    agentEvent('step.started', { sequence: 2, step_index: 1 }),
    {
      ...agentEvent('reasoning.snapshot', { sequence: 3 }),
      content: '“店铺”可能表示分组维度或筛选条件，需要先确认。',
    },
    {
      ...agentEvent('workflow.step', { sequence: 4 }),
      action: 'understanding_clarification',
      args_summary: { question: '请确认“店铺”在本次查询中的使用方式。' },
    },
    {
      ...agentEvent('clarification.required', { sequence: 5 }),
      clarification_id: 1,
      question: '请确认“店铺”在本次查询中的使用方式。',
    },
  ])

  assert.equal(flow.status, 'waiting')
  assert.equal(flow.steps[2].title, '澄清业务口径')
  assert.equal(flow.steps[2].toolName, 'understanding_clarification')
  assert.equal(flow.steps[2].status, 'waiting')
  assert.match(flow.headline, /澄清业务口径/)
})

test('本地澄清接受事件会立即解除等待态', () => {
  const flow = buildAgentFlow(
    {
      record_id: 717,
      run_id: 4,
      status: 'waiting_user',
      steps: [],
      events: [],
    },
    [
      {
        ...agentEvent('question.understood', { sequence: 1 }),
        rewritten_question: '今天店铺的客户数',
        validation: { status: 'clarification_required' },
      },
      agentEvent('step.started', { sequence: 2, step_index: 1 }),
      {
        ...agentEvent('workflow.step', { sequence: 3 }),
        action: 'understanding_clarification',
        args_summary: { question: '请确认“店铺”在本次查询中的使用方式。' },
      },
      {
        ...agentEvent('clarification.required', { sequence: 4 }),
        clarification_id: 1,
        question: '请确认“店铺”在本次查询中的使用方式。',
      },
      agentEvent('clarification.accepted', { sequence: 4.001, synthetic: true }),
    ],
    true
  )

  const clarificationStep = flow.steps.find(
    (step) => step.toolName === 'understanding_clarification'
  )
  assert.equal(clarificationStep?.status, 'success')
  assert.equal(flow.steps[0].status, 'running')
  assert.equal(flow.steps[0].title, '理解补充信息')
  assert.equal(flow.status, 'running')
  assert.match(flow.headline, /理解补充信息/)
})

test('模型规划尚未返回时会立即展示思考下一步', () => {
  const flow = buildAgentFlow(
    undefined,
    [
      {
        ...agentEvent('question.understood', { sequence: 1 }),
        rewritten_question: '今天按店铺查看客户数',
        validation: { status: 'valid' },
      },
      agentEvent('step.started', { sequence: 2, step_index: 2 }),
    ],
    true
  )

  assert.equal(flow.steps.length, 2)
  assert.equal(flow.steps[1].title, '思考下一步')
  assert.equal(flow.steps[1].status, 'running')
  assert.match(flow.headline, /思考下一步/)
})
