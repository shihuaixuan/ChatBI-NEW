import assert from 'node:assert/strict'
import test from 'node:test'

import { buildAgentFlow } from './agentTraceDisplay.ts'

test('完成的运行保留中间失败步骤，但整体展示为已完成并标记重试', () => {
  const flow = buildAgentFlow({
    record_id: 715,
    run_id: 2,
    status: 'finished',
    steps: [
      {
        index: 1,
        tool_name: 'compile_semantic_sql',
        status: 'success',
        result_summary: { sql: 'select 1' },
      },
      {
        index: 2,
        tool_name: 'execute_sql',
        status: 'failed',
        error: "Incorrect DATE value: 'today'",
      },
      {
        index: 3,
        tool_name: 'execute_sql',
        status: 'success',
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

test('流式事件能在工具结果返回前展示正在执行的节点', () => {
  const flow = buildAgentFlow(
    undefined,
    [
      {
        sequence: 1,
        type: 'question-understood',
        rewritten_question: '今天店铺的客户数',
        intent_type: 'metric_query',
        validation: { status: 'valid' },
      },
      { sequence: 2, type: 'step-started', step_index: 1 },
      {
        sequence: 3,
        type: 'thinking',
        content: '需要先检索客户数对应的指标口径，再决定查询字段。',
      },
      {
        sequence: 4,
        type: 'tool-called',
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
      { sequence: 1, type: 'step-started', step_index: 1 },
      { sequence: 2, type: 'tool-called', tool_name: 'execute_sql' },
      { sequence: 3, type: 'run-failed', content: '已达最大步数 12' },
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
      sequence: 1,
      type: 'question-understood',
      rewritten_question: '今天店铺的客户数',
      validation: { status: 'clarification_required' },
    },
    { sequence: 2, type: 'step-started', step_index: 1 },
    {
      sequence: 3,
      type: 'thinking',
      content: '“店铺”可能表示分组维度或筛选条件，需要先确认。',
    },
    {
      sequence: 4,
      type: 'workflow-step',
      action: 'understanding_clarification',
      args_summary: { question: '请确认“店铺”在本次查询中的使用方式。' },
    },
    {
      sequence: 5,
      type: 'clarification',
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
        sequence: 1,
        type: 'question-understood',
        rewritten_question: '今天店铺的客户数',
        validation: { status: 'clarification_required' },
      },
      { sequence: 2, type: 'step-started', step_index: 1 },
      {
        sequence: 3,
        type: 'workflow-step',
        action: 'understanding_clarification',
        args_summary: { question: '请确认“店铺”在本次查询中的使用方式。' },
      },
      {
        sequence: 4,
        type: 'clarification',
        clarification_id: 1,
        question: '请确认“店铺”在本次查询中的使用方式。',
      },
      {
        sequence: 4.001,
        type: 'clarification-accepted',
        synthetic: true,
      },
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
        sequence: 1,
        type: 'question-understood',
        rewritten_question: '今天按店铺查看客户数',
        validation: { status: 'valid' },
      },
      { sequence: 2, type: 'step-started', step_index: 2 },
    ],
    true
  )

  assert.equal(flow.steps.length, 2)
  assert.equal(flow.steps[1].title, '思考下一步')
  assert.equal(flow.steps[1].status, 'running')
  assert.match(flow.headline, /思考下一步/)
})
