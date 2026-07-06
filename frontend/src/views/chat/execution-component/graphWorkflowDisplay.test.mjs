import assert from 'node:assert/strict'
import test from 'node:test'

import { buildGraphWorkflowSteps } from './graphWorkflowDisplay.ts'

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
