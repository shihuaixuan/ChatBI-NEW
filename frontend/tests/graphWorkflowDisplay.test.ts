import * as assert from 'node:assert/strict'

import {
  buildGraphWorkflowStepsFromEvents,
  buildGraphInteractionResponse,
  buildGraphWorkflowSteps,
  buildVisibleGraphWorkflowSteps,
  graphProgressHeadline,
  graphInteractionControlsDisabled,
  graphInteractionOptionKey,
  graphLatestEventSequence,
  normalizeGraphInteraction,
  pendingInteractionFromGraphEvent,
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
    {
      name: 'ask_metric_selection',
      status: 'waiting_input',
      output: { prompt: '请选择要分析的指标。' },
    },
  ],
}

const succeededTrace = {
  run_id: 'run-2',
  status: 'succeeded',
  current_node: 'finish',
  nodes: [
    { name: 'classify_question', status: 'succeeded', output: { category: 'data' } },
    {
      name: 'recognize_intent',
      status: 'succeeded',
      output: {
        intent_type: 'metric_query',
        metric_mentions: ['访问人数'],
        dimension_slots: [
          { name: '档口', role: 'ambiguous', value: null, value_status: 'not_provided' },
        ],
        time_range: { raw: '今天', value_status: 'provided' },
      },
    },
    {
      name: 'generate_sql',
      status: 'succeeded',
      output: { sql: 'select count(*) as uv from visits' },
    },
    { name: 'execute_sql', status: 'succeeded', output: { row_count: 1, rows: [{ uv: 94400 }] } },
    {
      name: 'compose_final_reply',
      status: 'succeeded',
      output: { answer: '今天访问人数为 100。' },
    },
  ],
}

const waitingSteps = buildGraphWorkflowSteps(waitingTrace, waitingInteraction)
assert.equal(waitingSteps.at(-1)?.label, '选择分析指标')
assert.equal(waitingSteps.at(-1)?.status, 'waiting_input')
assert.equal(waitingSteps.at(-1)?.summary, '请选择要分析的指标。')
assert.equal(
  graphProgressHeadline(waitingTrace, waitingInteraction),
  '需要你补充信息：请选择要分析的指标。'
)

const normalized = normalizeGraphInteraction(waitingInteraction)
assert.equal(normalized.title, '请选择要分析的指标')
assert.equal(normalized.targetSlots[0], 'metric')
assert.notEqual(
  graphInteractionOptionKey(normalized.options[0], 0),
  graphInteractionOptionKey(normalized.options[1], 1)
)
assert.equal(
  graphInteractionOptionKey(normalized.options[0], 0),
  graphInteractionOptionKey(normalizeGraphInteraction(waitingInteraction).options[0], 0)
)
assert.deepEqual(buildGraphInteractionResponse(waitingInteraction, normalized.options[0]), {
  metric: '11',
})
assert.deepEqual(buildGraphInteractionResponse(waitingInteraction, undefined, '客流人数'), {
  metric: '客流人数',
})
assert.deepEqual(buildGraphInteractionResponse(waitingInteraction, undefined, '', true), {
  skipped: true,
})

const dimensionValueInteraction = {
  interaction_id: 'dimension-value-1',
  run_id: 'run-dimension-value',
  node_name: 'ask_slot_clarification',
  status: 'pending',
  prompt: '请确认“店铺”这个维度的使用方式。',
  response_schema: {
    type: 'object',
    properties: {
      dimension: { type: 'string' },
      dimension_usage: { type: 'string' },
      dimension_values: { type: 'object' },
      skipped: { type: 'boolean' },
    },
    'x-card': {
      clarification_type: 'dimension_usage',
      input_type: 'dimension_value_form',
      dimension_value_fields: ['店铺'],
    },
  },
  options: [
    { label: '按店铺分组查看', value: { dimension: '店铺', dimension_usage: 'group_by' } },
    {
      label: '筛选某个具体店铺',
      value: {
        dimension: '店铺',
        dimension_usage: 'filter_value_required',
        dimension_value_fields: ['店铺'],
      },
    },
  ],
}
const normalizedDimensionValue = normalizeGraphInteraction(dimensionValueInteraction)
assert.deepEqual(
  buildGraphInteractionResponse(
    dimensionValueInteraction,
    normalizedDimensionValue.options[1],
    { 店铺: '1' }
  ),
  {
    dimension: '店铺',
    dimension_usage: 'filter_value_required',
    dimension_value_fields: ['店铺'],
    dimension_values: { 店铺: '1' },
  }
)

const doneSteps = buildGraphWorkflowSteps(succeededTrace, null)
assert.equal(graphProgressHeadline(succeededTrace, null), '已完成')
assert.equal(doneSteps.at(-1)?.label, '整理回复')
assert.equal(doneSteps.at(-1)?.status, 'succeeded')
assert.equal(doneSteps.find((step) => step.key === 'execute_sql')?.summary, '返回 1 行')
assert.equal(
  doneSteps.find((step) => step.key === 'recognize_intent')?.summary,
  '分析类型：指标查询；指标：访问人数；维度：档口（未提供具体值）；时间：今天'
)
assert.equal(
  doneSteps.find((step) => step.key === 'generate_sql')?.details?.sql,
  'select count(*) as uv from visits'
)
assert.deepEqual(doneSteps.find((step) => step.key === 'execute_sql')?.details?.rows, [
  { uv: 94400 },
])

assert.deepEqual(buildVisibleGraphWorkflowSteps(succeededTrace, null, [], true), [])

const completedTraceWithAnsweredInteraction = {
  run_id: 'run-answered-slot',
  status: 'succeeded',
  current_node: 'finish',
  nodes: [
    { name: 'recognize_intent', status: 'succeeded', output: { intent_type: 'metric_query' } },
    {
      name: 'ask_slot_clarification',
      status: 'waiting_input',
      output: { prompt: '请确认“店铺”这个维度的使用方式。' },
    },
    { name: 'retrieve_knowledge', status: 'succeeded', output: { status: 'missed' } },
    { name: 'finish', status: 'succeeded', output: { value: true } },
  ],
}
const answeredInteractionSteps = buildGraphWorkflowSteps(
  completedTraceWithAnsweredInteraction,
  null
)
assert.equal(
  answeredInteractionSteps.find((step) => step.key === 'ask_slot_clarification')?.status,
  'succeeded'
)
assert.equal(
  graphProgressHeadline(completedTraceWithAnsweredInteraction, null, [
    { key: 'retrieve_knowledge', label: '匹配数据资产', status: 'running', summary: '' },
  ]),
  '已完成'
)

const liveSteps = buildGraphWorkflowStepsFromEvents(
  [
    { sequence: 1, event_type: 'run.started', public_payload: {} },
    { sequence: 2, event_type: 'node.started', node_name: 'classify_question', public_payload: {} },
    {
      sequence: 3,
      event_type: 'node.succeeded',
      node_name: 'classify_question',
      public_payload: { summary: { category: 'data' } },
    },
    {
      sequence: 4,
      event_type: 'node.routed',
      node_name: 'classify_question',
      public_payload: { target: 'retrieve_knowledge' },
    },
    {
      sequence: 5,
      event_type: 'node.started',
      node_name: 'retrieve_knowledge',
      public_payload: {},
    },
  ],
  null,
  null
)
assert.equal(liveSteps[0]?.label, '问题分类')
assert.equal(liveSteps[0]?.status, 'succeeded')
assert.equal(liveSteps[0]?.summary, '识别为数据查询')
assert.equal(liveSteps[1]?.label, '匹配数据资产')
assert.equal(liveSteps[1]?.status, 'running')
assert.equal(graphProgressHeadline(null, null, liveSteps), '正在执行：匹配数据资产...')

const standardizedQuestionSteps = buildGraphWorkflowSteps({
  run_id: 'run-standardized-question',
  status: 'succeeded',
  current_node: 'finish',
  nodes: [
    { name: 'classify_question', status: 'succeeded', output: { category: 'data' } },
    {
      name: 'rewrite_question',
      status: 'succeeded',
      output: { rewritten_question: '所有档口的访问人数' },
    },
  ],
})
assert.equal(standardizedQuestionSteps[0]?.label, '问题分类')
assert.equal(standardizedQuestionSteps[1]?.label, '问题标准化')

const resumedSteps = buildGraphWorkflowStepsFromEvents(
  [
    {
      sequence: 1,
      event_type: 'run.waiting_input',
      node_name: 'ask_slot_clarification',
      public_payload: {
        pending_interaction: {
          interaction_id: 'slot-1',
          run_id: 'run-slot',
          node_name: 'ask_slot_clarification',
          status: 'pending',
          prompt: '请确认“店铺”这个维度的使用方式。',
        },
      },
    },
    { sequence: 2, event_type: 'run.resumed', public_payload: {} },
    {
      sequence: 3,
      event_type: 'node.started',
      node_name: 'retrieve_knowledge',
      public_payload: {},
    },
  ],
  null,
  null
)
assert.equal(
  resumedSteps.find((step) => step.key === 'ask_slot_clarification')?.status,
  'succeeded'
)
assert.equal(
  graphLatestEventSequence(
    [{ sequence: 7, event_type: 'run.waiting_input' }],
    [
      {
        sequence: 7.001,
        event_type: 'run.resumed',
        public_payload: { synthetic: true },
      },
    ]
  ),
  7
)

const runningTraceAfterAnsweredSlot = {
  run_id: 'run-slot-running',
  status: 'running',
  current_node: 'generate_question_answer',
  nodes: [
    { name: 'recognize_intent', status: 'succeeded', output: { intent_type: 'metric_query' } },
    {
      name: 'ask_slot_clarification',
      status: 'succeeded',
      output: { prompt: '请确认“店铺”这个维度的使用方式。' },
    },
    { name: 'retrieve_knowledge', status: 'succeeded', output: { status: 'missed' } },
    { name: 'generate_question_answer', status: 'started', output: {} },
  ],
}
const runningStepsAfterAnsweredSlot = buildGraphWorkflowStepsFromEvents(
  [
    {
      sequence: 1,
      event_type: 'run.waiting_input',
      node_name: 'ask_slot_clarification',
      public_payload: {
        pending_interaction: {
          interaction_id: 'slot-running-1',
          run_id: 'run-slot-running',
          node_name: 'ask_slot_clarification',
          status: 'pending',
          prompt: '请确认“店铺”这个维度的使用方式。',
        },
      },
    },
    {
      sequence: 2,
      event_type: 'run.resumed',
      node_name: 'ask_slot_clarification',
      public_payload: { synthetic: true },
    },
    {
      sequence: 3,
      event_type: 'node.started',
      node_name: 'generate_question_answer',
      public_payload: {},
    },
  ],
  runningTraceAfterAnsweredSlot,
  null
)
assert.equal(
  runningStepsAfterAnsweredSlot.find((step) => step.key === 'ask_slot_clarification')?.status,
  'succeeded'
)

const waitingInputEvent = {
  sequence: 6,
  event_type: 'run.waiting_input',
  node_name: 'ask_metric_selection',
  public_payload: {
    pending_interaction: waitingInteraction,
  },
}
assert.deepEqual(pendingInteractionFromGraphEvent(waitingInputEvent), waitingInteraction)
assert.equal(graphInteractionControlsDisabled(true, true), false)
assert.equal(graphInteractionControlsDisabled(true, false), true)
assert.equal(
  graphLatestEventSequence(
    [{ sequence: 5, event_type: 'node.succeeded' }],
    [{ sequence: 7, event_type: 'run.waiting_input' }]
  ),
  7
)

const mergedCompletedSteps = buildGraphWorkflowStepsFromEvents(
  [
    {
      sequence: 1,
      event_type: 'node.succeeded',
      node_name: 'generate_sql',
      public_payload: { summary: { sql: 'select count(*) as uv from visits' } },
    },
    {
      sequence: 2,
      event_type: 'node.succeeded',
      node_name: 'execute_sql',
      public_payload: { summary: { row_count: 1, rows: [{ uv: 94400 }] } },
    },
  ],
  {
    run_id: 'run-3',
    status: 'succeeded',
    current_node: 'finish',
    nodes: [
      { name: 'generate_sql', status: 'succeeded', output: { sql: null } },
      { name: 'execute_sql', status: 'succeeded', output: { row_count: 1 } },
    ],
  },
  null
)
assert.equal(
  mergedCompletedSteps.find((step) => step.key === 'generate_sql')?.details?.sql,
  'select count(*) as uv from visits'
)
assert.deepEqual(mergedCompletedSteps.find((step) => step.key === 'execute_sql')?.details?.rows, [
  { uv: 94400 },
])

console.info('graphWorkflowDisplay tests passed')
