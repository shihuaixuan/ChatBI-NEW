import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import type { AgentTraceNode } from '../src/api/agent-chat.ts'
import {
  defaultExpandedTraceNodeIds,
  filterAgentTraceTree,
  projectAgentTraceTree,
  shouldPollAgentTrace,
} from '../src/views/chat/execution-component/agentTraceProjection.ts'

const currentDirectory = dirname(fileURLToPath(import.meta.url))

function traceNode(
  id: number,
  parentId: number | null,
  sequence: number,
  overrides: Partial<AgentTraceNode> = {}
): AgentTraceNode {
  return {
    id,
    parent_id: parentId,
    node_key: `node:${id}`,
    node_type: 'phase',
    name: `node_${id}`,
    display_name: `节点 ${id}`,
    status: 'succeeded',
    sequence,
    started_at: `2026-08-12T12:00:0${sequence}.000Z`,
    finished_at: `2026-08-12T12:00:0${sequence + 2}.000Z`,
    latency_ms: 2000,
    token_usage: {},
    input_summary: {},
    output_summary: {},
    metadata: {},
    has_input_detail: false,
    has_output_detail: false,
    ...overrides,
  }
}

test('扁平节点按 parent_id 和 sequence 投影为调用树', () => {
  const projection = projectAgentTraceTree([
    traceNode(3, 1, 3),
    traceNode(1, null, 1, { node_type: 'run' }),
    traceNode(2, 1, 2),
  ])

  assert.deepEqual(
    projection.roots.map((node) => node.id),
    [1]
  )
  assert.deepEqual(
    projection.roots[0].children.map((node) => node.id),
    [2, 3]
  )
  assert.equal(projection.byId.get(2)?.depth, 1)
  assert.deepEqual(projection.integrityErrors, [])
})

test('父节点缺失时节点仍可见并标记数据异常', () => {
  const projection = projectAgentTraceTree([traceNode(2, 99, 2)])

  assert.deepEqual(
    projection.roots.map((node) => node.id),
    [2]
  )
  assert.equal(projection.roots[0].integrity_error, '父节点 99 不存在')
  assert.match(projection.integrityErrors[0], /父节点 99 不存在/)
})

test('默认展开根节点、一级节点以及失败节点的祖先链', () => {
  const projection = projectAgentTraceTree([
    traceNode(1, null, 1, { node_type: 'run' }),
    traceNode(2, 1, 2),
    traceNode(3, 2, 3),
    traceNode(4, 3, 4, { status: 'failed' }),
  ])

  assert.deepEqual([...defaultExpandedTraceNodeIds(projection)].sort(), [1, 2, 3, 4])
})

test('时间区间重叠的同级节点标记为并行', () => {
  const projection = projectAgentTraceTree([
    traceNode(1, null, 1, { node_type: 'run' }),
    traceNode(2, 1, 2, {
      started_at: '2026-08-12T12:00:02.000Z',
      finished_at: '2026-08-12T12:00:05.000Z',
    }),
    traceNode(3, 1, 3, {
      started_at: '2026-08-12T12:00:03.000Z',
      finished_at: '2026-08-12T12:00:04.000Z',
    }),
  ])

  assert.equal(projection.byId.get(2)?.parallel, true)
  assert.equal(projection.byId.get(3)?.parallel, true)
})

test('搜索、类型和异常筛选会保留命中节点的祖先', () => {
  const projection = projectAgentTraceTree([
    traceNode(1, null, 1, { node_type: 'run' }),
    traceNode(2, 1, 2, { node_type: 'llm', display_name: '意图识别' }),
    traceNode(3, 1, 3, { node_type: 'tool', display_name: '执行 SQL', status: 'failed' }),
  ])

  const searched = filterAgentTraceTree(projection.roots, 'SQL', 'tool', true)

  assert.deepEqual(
    searched.map((node) => node.id),
    [1]
  )
  assert.deepEqual(
    searched[0].children.map((node) => node.id),
    [3]
  )
})

test('只在执行中轮询，等待和终态停止轮询', () => {
  assert.equal(shouldPollAgentTrace('running'), true)
  for (const status of ['waiting', 'succeeded', 'failed', 'cancelled', 'partial']) {
    assert.equal(shouldPollAgentTrace(status), false)
  }
})

test('Agent 执行详情由统一入口明确路由且保留 unavailable 展示', () => {
  const executionDetailsSource = readFileSync(
    resolve(currentDirectory, '../src/views/chat/ExecutionDetails.vue'),
    'utf8'
  )
  const tokenTimeSource = readFileSync(
    resolve(currentDirectory, '../src/views/chat/ChatTokenTime.vue'),
    'utf8'
  )
  const traceDetailsSource = readFileSync(
    resolve(currentDirectory, '../src/views/chat/AgentTraceDetails.vue'),
    'utf8'
  )

  assert.match(executionDetailsSource, /executionType === 'agent'/)
  assert.match(executionDetailsSource, /agentTraceDetailsRef\.value\?\.open\(recordId\)/)
  assert.match(tokenTimeSource, /getLogList\(props\.recordId, props\.executionType\)/)
  assert.match(traceDetailsSource, /v-if="!trace\.available"/)
  assert.match(traceDetailsSource, /if \(!canLoadDetail\.value\) return/)
})
