import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildGraphWorkflowSteps,
  buildGraphWorkflowStepsFromEvents,
  buildVisibleGraphWorkflowSteps,
} from './graphWorkflowDisplay.ts'

test('节点名称优先使用 trace 返回的后端 label', () => {
  const steps = buildGraphWorkflowSteps({
    run_id: 'run-label',
    status: 'succeeded',
    current_node: 'custom_node',
    nodes: [
      {
        name: 'custom_node',
        label: '后端节点标签',
        status: 'succeeded',
        output: {},
      },
    ],
  })

  assert.equal(steps[0].label, '后端节点标签')
})

test('未知后端 label 时展示节点名而不是前端手抄表', () => {
  const steps = buildGraphWorkflowSteps({
    run_id: 'run-no-label',
    status: 'succeeded',
    current_node: 'classify_question',
    nodes: [
      {
        name: 'classify_question',
        status: 'succeeded',
        output: {},
      },
    ],
  })

  assert.equal(steps[0].label, 'classify_question')
})

test('流式事件节点名称读取事件里的后端 label', () => {
  const steps = buildGraphWorkflowStepsFromEvents([
    {
      sequence: 1,
      event_type: 'node.started',
      node_name: 'generate_sql',
      public_payload: { label: '后端生成查询' },
    },
  ])

  assert.equal(steps[0].label, '后端生成查询')
})

test('构建查询计划展示指标维度筛选摘要', () => {
  const steps = buildGraphWorkflowSteps({
    run_id: 'run-plan',
    status: 'succeeded',
    current_node: 'finish',
    nodes: [
      {
        name: 'bind_query_plan',
        label: '构建查询计划',
        status: 'succeeded',
        output: {
          status: 'ready',
          strategy: 'semantic_compiler',
          select_mode: 'aggregate',
          metrics: [{ display_name: '客户数' }],
          group_bys: [{ display_name: '档口' }],
          filters: [{ display_name: '档口ID', operator: '=', value: 1 }],
        },
      },
    ],
  })

  assert.equal(steps[0].summary, '指标：客户数；维度：档口；筛选：档口ID=1')
})

test('生成查询展示 SQL 明细', () => {
  const sql = 'select count(*) as customer_count from customers where stall_id = 1'
  const steps = buildGraphWorkflowSteps({
    run_id: 'run-sql',
    status: 'succeeded',
    current_node: 'finish',
    nodes: [
      {
        name: 'generate_sql',
        label: '生成查询',
        status: 'succeeded',
        output: {
          statement_type: 'select',
          sql,
          artifact_ref: null,
        },
      },
    ],
  })

  assert.equal(steps[0].summary, '已生成 SQL')
  assert.equal(steps[0].details?.sql, sql)
})

test('事件优先展示生成查询节点的 SQL 明细', () => {
  const eventSql = 'select count(*) as customer_count from customers where merchant_id = 1'
  const steps = buildVisibleGraphWorkflowSteps(
    {
      run_id: 'run-final-trace',
      status: 'succeeded',
      current_node: 'finish',
      nodes: [
        {
          name: 'generate_sql',
          label: '生成查询',
          status: 'succeeded',
          output: {
            statement_type: 'select',
            sql: 'select 1',
            artifact_ref: null,
          },
        },
      ],
    },
    null,
    [
      {
        sequence: 1,
        event_type: 'node.succeeded',
        node_name: 'generate_sql',
        public_payload: {
          label: '生成查询',
          summary: {
            statement_type: 'select',
            sql: eventSql,
            artifact_ref: null,
          },
        },
      },
    ],
    false
  )

  assert.equal(steps[0].summary, '已生成 SQL')
  assert.equal(steps[0].details?.sql, eventSql)
})

test('查询数据展示单查询样本行', () => {
  const steps = buildGraphWorkflowSteps({
    run_id: 'run-execute',
    status: 'succeeded',
    current_node: 'finish',
    nodes: [
      {
        name: 'execute_sql',
        label: '查询数据',
        status: 'succeeded',
        output: {
          status: 'succeeded',
          row_count: 1,
          fields: ['customer_count'],
          results: [
            {
              query_id: 'query-0',
              status: 'succeeded',
              row_count: 1,
              fields: ['customer_count'],
              sample_rows: [{ customer_count: 12 }],
            },
          ],
        },
      },
    ],
  })

  assert.equal(steps[0].summary, '返回 1 行')
  assert.deepEqual(steps[0].details?.rows, [{ customer_count: 12 }])
  assert.deepEqual(steps[0].details?.columns, ['customer_count'])
})

test('拆分查询优先读取统一 execution results', () => {
  const steps = buildGraphWorkflowSteps({
    run_id: 'run-1',
    status: 'succeeded',
    current_node: 'finish',
    nodes: [
      {
        name: 'execute_split_queries',
        status: 'succeeded',
        output: {
          status: 'succeeded',
          row_count: 3,
          results: [
            {
              query_id: 'query-0',
              status: 'succeeded',
              row_count: 1,
              fields: ['value'],
              sample_rows: [{ value: 1 }],
            },
            {
              query_id: 'query-1',
              status: 'succeeded',
              row_count: 2,
              fields: ['value'],
              sample_rows: [{ value: 2 }],
            },
          ],
        },
      },
    ],
  })

  assert.equal(steps[0].summary, '2 条查询，共返回 3 行')
  assert.deepEqual(steps[0].details?.queries?.[0].rows, [{ value: 1 }])
  assert.deepEqual(steps[0].details?.queries?.[1].rows, [{ value: 2 }])
})
