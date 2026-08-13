import type { AgentTraceNode } from '@/api/agent-chat'

export type AgentTraceNodeType =
  | 'run'
  | 'invocation'
  | 'phase'
  | 'llm'
  | 'tool'
  | 'validation'
  | 'projection'
  | 'persistence'
  | 'interaction'

export interface AgentTraceTreeNode extends AgentTraceNode {
  children: AgentTraceTreeNode[]
  depth: number
  parallel: boolean
  integrity_error?: string
}

export interface AgentTraceTreeProjection {
  roots: AgentTraceTreeNode[]
  byId: Map<number, AgentTraceTreeNode>
  integrityErrors: string[]
}

const attentionStatuses = new Set([
  'running',
  'waiting',
  'failed',
  'rejected',
  'interrupted',
  'partial',
])

export const agentTraceTypeOptions: Array<{ value: AgentTraceNodeType; label: string }> = [
  { value: 'llm', label: '仅看模型' },
  { value: 'tool', label: '仅看工具' },
]

export function projectAgentTraceTree(nodes: AgentTraceNode[]): AgentTraceTreeProjection {
  const ordered = [...nodes].sort((left, right) => left.sequence - right.sequence)
  const byId = new Map<number, AgentTraceTreeNode>()
  const integrityErrors: string[] = []

  for (const node of ordered) {
    if (byId.has(node.id)) {
      integrityErrors.push(`节点 ${node.id} 重复，已忽略后出现的记录。`)
      continue
    }
    byId.set(node.id, {
      ...node,
      children: [],
      depth: 0,
      parallel: Boolean(node.metadata.parallel || node.metadata.parallel_group),
    })
  }

  const roots: AgentTraceTreeNode[] = []
  for (const node of byId.values()) {
    if (node.parent_id === null) {
      roots.push(node)
      continue
    }
    const parent = byId.get(node.parent_id)
    if (!parent) {
      node.integrity_error = `父节点 ${node.parent_id} 不存在`
      integrityErrors.push(`节点 ${node.id} 的父节点 ${node.parent_id} 不存在。`)
      roots.push(node)
      continue
    }
    if (createsCycle(node, parent, byId)) {
      node.integrity_error = '父子关系存在循环'
      integrityErrors.push(`节点 ${node.id} 的父子关系存在循环。`)
      roots.push(node)
      continue
    }
    parent.children.push(node)
  }

  const reachable = new Set<number>()
  for (const root of roots) setTreeDepth(root, 0, reachable)
  for (const node of byId.values()) {
    if (reachable.has(node.id)) continue
    node.integrity_error = node.integrity_error || '节点无法从根节点到达'
    integrityErrors.push(`节点 ${node.id} 无法从根节点到达。`)
    roots.push(node)
    setTreeDepth(node, 0, reachable)
  }

  for (const node of byId.values()) markParallelChildren(node)
  roots.sort((left, right) => left.sequence - right.sequence)
  return { roots, byId, integrityErrors }
}

export function defaultExpandedTraceNodeIds(projection: AgentTraceTreeProjection): Set<number> {
  const expanded = new Set<number>()
  const parentById = new Map<number, number>()

  for (const root of projection.roots) {
    expanded.add(root.id)
    for (const child of root.children) expanded.add(child.id)
  }
  for (const node of projection.byId.values()) {
    if (node.parent_id !== null) parentById.set(node.id, node.parent_id)
    if (!attentionStatuses.has(node.status)) continue
    let current: AgentTraceTreeNode | undefined = node
    const visited = new Set<number>()
    while (current && !visited.has(current.id)) {
      visited.add(current.id)
      expanded.add(current.id)
      const parentId = parentById.get(current.id)
      current = parentId === undefined ? undefined : projection.byId.get(parentId)
    }
  }
  return expanded
}

export function filterAgentTraceTree(
  roots: AgentTraceTreeNode[],
  searchText: string,
  nodeType: AgentTraceNodeType | 'all',
  failedOnly: boolean
): AgentTraceTreeNode[] {
  const normalizedSearch = searchText.trim().toLocaleLowerCase()

  function visit(node: AgentTraceTreeNode): AgentTraceTreeNode | undefined {
    const children = node.children
      .map(visit)
      .filter((child): child is AgentTraceTreeNode => child !== undefined)
    const searchMatches =
      !normalizedSearch ||
      node.display_name.toLocaleLowerCase().includes(normalizedSearch) ||
      node.name.toLocaleLowerCase().includes(normalizedSearch) ||
      node.node_key.toLocaleLowerCase().includes(normalizedSearch)
    const typeMatches = nodeType === 'all' || node.node_type === nodeType
    const failureMatches =
      !failedOnly || ['failed', 'rejected', 'interrupted', 'partial'].includes(node.status)
    if (!(searchMatches && typeMatches && failureMatches) && children.length === 0) {
      return undefined
    }
    return { ...node, children }
  }

  return roots.map(visit).filter((node): node is AgentTraceTreeNode => node !== undefined)
}

export function traceStatusText(status: string): string {
  return (
    {
      running: '执行中',
      succeeded: '成功',
      failed: '失败',
      rejected: '已拒绝',
      interrupted: '已中断',
      waiting: '等待用户',
      cancelled: '已取消',
      skipped: '已跳过',
      partial: '部分缺失',
    }[status] || status
  )
}

export function traceNodeTypeText(nodeType: string): string {
  return (
    {
      run: '流程',
      invocation: '调用',
      phase: '阶段',
      llm: '模型',
      tool: '工具',
      validation: '校验',
      projection: '整理',
      persistence: '持久化',
      interaction: '交互',
    }[nodeType] || nodeType
  )
}

export function shouldPollAgentTrace(status: string): boolean {
  return status === 'running'
}

function createsCycle(
  node: AgentTraceTreeNode,
  parent: AgentTraceTreeNode,
  byId: Map<number, AgentTraceTreeNode>
): boolean {
  const visited = new Set<number>([node.id])
  let current: AgentTraceTreeNode | undefined = parent
  while (current) {
    if (visited.has(current.id)) return true
    visited.add(current.id)
    current = current.parent_id === null ? undefined : byId.get(current.parent_id)
  }
  return false
}

function setTreeDepth(node: AgentTraceTreeNode, depth: number, visited: Set<number>): void {
  if (visited.has(node.id)) return
  visited.add(node.id)
  node.depth = depth
  node.children.sort((left, right) => left.sequence - right.sequence)
  for (const child of node.children) setTreeDepth(child, depth + 1, visited)
}

function markParallelChildren(parent: AgentTraceTreeNode): void {
  if (parent.name === 'parallel_understanding') {
    for (const child of parent.children) child.parallel = true
  }
  for (let index = 0; index < parent.children.length; index += 1) {
    for (let otherIndex = index + 1; otherIndex < parent.children.length; otherIndex += 1) {
      const left = parent.children[index]
      const right = parent.children[otherIndex]
      if (executionIntervalsOverlap(left, right)) {
        left.parallel = true
        right.parallel = true
      }
    }
  }
}

function executionIntervalsOverlap(left: AgentTraceTreeNode, right: AgentTraceTreeNode): boolean {
  const leftStart = Date.parse(left.started_at)
  const rightStart = Date.parse(right.started_at)
  const leftEnd = left.finished_at ? Date.parse(left.finished_at) : Number.POSITIVE_INFINITY
  const rightEnd = right.finished_at ? Date.parse(right.finished_at) : Number.POSITIVE_INFINITY
  if ([leftStart, rightStart, leftEnd, rightEnd].some(Number.isNaN)) return false
  return leftStart < rightEnd && rightStart < leftEnd
}
