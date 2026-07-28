import type { AgentTimelineResponse, AgentTimelineToolCall } from '@/api/agent-chat'
import { normalizeAgentEvent } from '../answer/agentEventReducer.ts'

export type AgentFlowStatus = 'running' | 'success' | 'failed' | 'waiting'

export interface AgentFlowStep {
  key: string
  index?: number
  kind: 'understanding' | 'thinking' | 'tool'
  title: string
  status: AgentFlowStatus
  toolName?: string
  toolCallId?: string
  stepId?: number
  latencyMs?: number
  args: Record<string, any>
  result: Record<string, any>
  error?: string
  thinking?: string
  understanding?: Record<string, any>
}

export interface AgentFlowView {
  status: AgentFlowStatus
  headline: string
  steps: AgentFlowStep[]
  failedCount: number
}

const TOOL_TITLES: Record<string, string> = {
  search_semantic_assets: '检索语义资产',
  get_dataset_schema: '读取数据结构',
  search_terminology: '检索业务术语',
  get_sql_examples: '查找 SQL 示例',
  clarify: '澄清业务口径',
  understanding_clarification: '澄清业务口径',
  compile_semantic_sql: '编译语义 SQL',
  validate_sql: '校验 SQL',
  execute_sql: '执行查询',
  finish: '生成回答',
}

export function agentToolTitle(toolName?: string) {
  if (!toolName) return '规划下一步'
  return TOOL_TITLES[toolName] || toolName
}

export function buildAgentFlow(
  timeline?: AgentTimelineResponse,
  liveEvents: Array<Record<string, any>> = [],
  runtimeLoading = false
): AgentFlowView {
  const events = liveEvents.length ? liveEvents : timeline?.events || []
  const toolSteps = new Map<string, AgentFlowStep>()
  const workflowSteps = new Map<number, AgentFlowStep>()
  const thinkingSteps = new Map<number, AgentFlowStep>()
  const stepIdToIndex = new Map<number, number>()
  let understandingStep: AgentFlowStep | undefined
  let currentIndex: number | undefined
  let currentStepId: number | undefined
  let terminalStatus: 'success' | 'failed' | undefined
  let eventRunStatus: 'running' | 'waiting_user' | 'finished' | 'failed' | 'cancelled' | undefined

  for (const persisted of timeline?.steps || []) {
    if (persisted.id !== undefined) stepIdToIndex.set(persisted.id, persisted.index)
  }
  for (const persisted of timeline?.tool_calls || []) {
    toolSteps.set(
      persisted.tool_call_id,
      persistedToolCall(persisted, stepIdToIndex.get(persisted.step_id))
    )
  }

  for (const rawEvent of [...events].sort(compareEvents)) {
    const event = normalizeAgentEvent(rawEvent)
    switch (event.domain) {
      case 'run.started':
        eventRunStatus = 'running'
        break
      case 'question.understood':
        eventRunStatus =
          event.validation?.status === 'clarification_required' ? 'waiting_user' : 'running'
        understandingStep = {
          key: 'question-understood',
          kind: 'understanding',
          title: '理解问题',
          status: event.validation?.status === 'clarification_required' ? 'waiting' : 'success',
          args: {},
          result: {},
          understanding: event,
        }
        break
      case 'step.started':
        eventRunStatus = 'running'
        currentIndex = Number(event.step_index)
        if (!Number.isFinite(currentIndex)) break
        currentStepId = Number(event.step_id)
        if (Number.isFinite(currentStepId)) {
          stepIdToIndex.set(currentStepId, currentIndex)
        } else {
          currentStepId = undefined
        }
        // step-started 在模型调用前发出，此时工具尚未确定，应先展示规划状态。
        ensureThinkingStep(thinkingSteps, currentIndex).status = 'running'
        break
      case 'reasoning.snapshot': {
        if (currentIndex === undefined) break
        const step = ensureThinkingStep(thinkingSteps, currentIndex)
        const content = String(event.content || '').trim()
        if (content) {
          step.thinking = step.thinking ? `${step.thinking}\n${content}` : content
        }
        step.status = 'success'
        break
      }
      case 'tool.called': {
        finishThinkingStep(thinkingSteps, currentIndex)
        const step = ensureToolCallStep(
          toolSteps,
          event,
          currentIndex,
          currentStepId,
          stepIdToIndex
        )
        step.toolName = event.tool_name
        step.title = agentToolTitle(event.tool_name)
        step.args = { ...step.args, ...(event.args_summary || {}) }
        break
      }
      case 'workflow.step': {
        finishThinkingStep(thinkingSteps, currentIndex)
        const step = currentWorkflowStep(workflowSteps, currentIndex)
        if (step) {
          step.toolName = event.action
          step.title = agentToolTitle(event.action)
          step.args = { ...step.args, ...(event.args_summary || {}) }
        }
        break
      }
      case 'tool.completed': {
        const step = findToolCallStep(toolSteps, event.tool_call_id)
        if (step) {
          step.result = { ...step.result, ...(event.result_summary || {}), ...event }
          step.status = 'success'
          step.latencyMs = event.latency_ms ?? step.latencyMs
        }
        break
      }
      case 'tool.failed': {
        const step = findToolCallStep(toolSteps, event.tool_call_id)
        if (step) {
          step.result = { ...step.result, ...(event.result_summary || {}), ...event }
          step.status = 'failed'
          step.error = String(event.error_code || event.result_summary?.error_code || '')
          step.latencyMs = event.latency_ms ?? step.latencyMs
        }
        break
      }
      case 'sql.generated':
      case 'sql.validated': {
        const step = findToolCallStep(toolSteps, event.tool_call_id)
        if (step && event.sql) step.result.sql = event.sql
        break
      }
      case 'sql.executed': {
        const step = findToolCallStep(toolSteps, event.tool_call_id)
        if (step) {
          step.result.row_count = event.row_count
          step.result.fields = event.fields
        }
        break
      }
      case 'clarification.required': {
        eventRunStatus = 'waiting_user'
        const step = event.tool_call_id
          ? findToolCallStep(toolSteps, event.tool_call_id)
          : currentWorkflowStep(workflowSteps, currentIndex)
        if (step) {
          step.status = 'waiting'
          step.result = { ...step.result, ...event }
        }
        break
      }
      case 'clarification.accepted': {
        eventRunStatus = 'running'
        if (understandingStep) {
          // 澄清提交后后端会重新理解完整问题，用运行态覆盖旧的等待结果。
          understandingStep.title = '理解补充信息'
          understandingStep.status = 'running'
          understandingStep.understanding = undefined
        }
        const waitingStep = [...toolSteps.values(), ...workflowSteps.values()]
          .reverse()
          .find((step) => step.status === 'waiting')
        if (waitingStep) waitingStep.status = 'success'
        break
      }
      case 'run.failed': {
        eventRunStatus = 'failed'
        terminalStatus = 'failed'
        const thinkingStep =
          currentIndex === undefined ? undefined : thinkingSteps.get(currentIndex)
        if (thinkingStep?.status === 'running') {
          thinkingStep.status = 'failed'
          thinkingStep.error = String(event.content || '')
        }
        const step = [...toolSteps.values(), ...workflowSteps.values()]
          .reverse()
          .find((item) => item.status === 'running')
        if (step) {
          step.status = 'failed'
          step.error = String(event.content || '')
        }
        break
      }
      case 'run.cancelled':
        eventRunStatus = 'cancelled'
        terminalStatus = 'failed'
        for (const step of toolSteps.values()) {
          if (step.status === 'running') step.status = 'failed'
        }
        break
      case 'run.finished':
        eventRunStatus = 'finished'
        terminalStatus = 'success'
        for (const step of thinkingSteps.values()) {
          if (step.status === 'running') step.status = 'success'
        }
        for (const step of toolSteps.values()) {
          if (step.status === 'running') step.status = 'success'
        }
        for (const step of workflowSteps.values()) {
          if (step.status === 'running') step.status = 'success'
        }
        break
    }
  }

  // 同一轮先展示模型为什么这样做，再展示实际工具调用，保留 Agent 的决策脉络。
  const steps: AgentFlowStep[] = understandingStep ? [understandingStep] : []
  const indexedTools = [...toolSteps.values(), ...workflowSteps.values()]
  const stepIndexes = [
    ...new Set([
      ...thinkingSteps.keys(),
      ...indexedTools
        .map((step) => step.index)
        .filter((index): index is number => index !== undefined),
    ]),
  ].sort((a, b) => a - b)
  for (const stepIndex of stepIndexes) {
    const thinkingStep = thinkingSteps.get(stepIndex)
    if (thinkingStep) steps.push(thinkingStep)
    for (const toolStep of indexedTools.filter((item) => item.index === stepIndex)) {
      steps.push(toolStep)
    }
  }
  for (const toolStep of indexedTools.filter((item) => item.index === undefined)) {
    steps.push(toolStep)
  }
  if (!steps.length && runtimeLoading) {
    steps.push({
      key: 'question-understanding-running',
      kind: 'understanding',
      title: '理解问题',
      status: 'running',
      args: {},
      result: {},
    })
  }

  const failedCount = steps.filter((step) => step.status === 'failed').length
  const waiting = [...steps].reverse().find((step) => step.status === 'waiting')
  const running = [...steps].reverse().find((step) => step.status === 'running')
  // SSE 事件比挂载或刷新时获取的 trace 快照更新，运行状态必须遵循同一优先级。
  const runStatus = eventRunStatus || String(timeline?.status || '')
  if (waiting || runStatus === 'waiting_user') {
    return {
      status: 'waiting',
      headline: `等待补充：${waiting?.title || '业务口径'}`,
      steps,
      failedCount,
    }
  }
  // 流式终止事件比轮询得到的旧状态更新，必须优先收口，避免失败后仍显示运行中。
  if (terminalStatus === 'failed' || runStatus === 'failed' || runStatus === 'cancelled') {
    const failed = [...steps].reverse().find((step) => step.status === 'failed')
    return {
      status: 'failed',
      headline: `执行失败：${failed?.title || '运行异常'}`,
      steps,
      failedCount,
    }
  }
  if (terminalStatus !== 'success' && (runtimeLoading || running || runStatus === 'running')) {
    return {
      status: 'running',
      headline: `正在执行：${running?.title || '准备数据'}`,
      steps,
      failedCount,
    }
  }
  const retryText = failedCount ? `，期间重试 ${failedCount} 次` : ''
  const toolCount = toolSteps.size + workflowSteps.size
  return {
    status: 'success',
    headline: `执行完成，共 ${toolCount} 个工具调用${retryText}`,
    steps,
    failedCount,
  }
}

function persistedToolCall(toolCall: AgentTimelineToolCall, stepIndex?: number): AgentFlowStep {
  return {
    key: `tool-${toolCall.tool_call_id}`,
    index: stepIndex,
    kind: 'tool',
    title: agentToolTitle(toolCall.tool_name),
    status: normalizeStatus(toolCall.status),
    toolName: toolCall.tool_name,
    toolCallId: toolCall.tool_call_id,
    stepId: toolCall.step_id,
    latencyMs: toolCall.latency_ms,
    args: toolCall.args_summary || {},
    result: toolCall.result_summary || {},
    error: toolCall.error_code,
  }
}

function ensureToolCallStep(
  steps: Map<string, AgentFlowStep>,
  event: Record<string, any>,
  currentIndex: number | undefined,
  currentStepId: number | undefined,
  stepIdToIndex: Map<number, number>
) {
  const toolCallId = String(
    event.tool_call_id || `${event.step_id || currentStepId || currentIndex}:${event.tool_name}`
  )
  let step = steps.get(toolCallId)
  if (!step) {
    const stepId = Number(event.step_id || currentStepId)
    const index = Number.isFinite(stepId) ? stepIdToIndex.get(stepId) || currentIndex : currentIndex
    step = {
      key: `tool-${toolCallId}`,
      index,
      kind: 'tool',
      title: agentToolTitle(event.tool_name),
      status: 'running',
      toolName: event.tool_name,
      toolCallId,
      stepId: Number.isFinite(stepId) ? stepId : undefined,
      args: {},
      result: {},
    }
    steps.set(toolCallId, step)
  }
  return step
}

function findToolCallStep(steps: Map<string, AgentFlowStep>, toolCallId?: string) {
  if (toolCallId) return steps.get(String(toolCallId))
  return [...steps.values()].reverse()[0]
}

function currentWorkflowStep(steps: Map<number, AgentFlowStep>, index?: number) {
  const resolvedIndex = index ?? 0
  let step = steps.get(resolvedIndex)
  if (!step) {
    step = {
      key: `workflow-${resolvedIndex}`,
      index,
      kind: 'tool',
      title: '规划下一步',
      status: 'running',
      args: {},
      result: {},
    }
    steps.set(resolvedIndex, step)
  }
  return step
}

function ensureThinkingStep(steps: Map<number, AgentFlowStep>, index: number) {
  let step = steps.get(index)
  if (!step) {
    step = {
      key: `thinking-${index}`,
      index,
      kind: 'thinking',
      title: '思考下一步',
      status: 'running',
      args: {},
      result: {},
    }
    steps.set(index, step)
  }
  return step
}

function finishThinkingStep(steps: Map<number, AgentFlowStep>, index?: number) {
  if (index === undefined) return
  const step = steps.get(index)
  if (step?.status === 'running') step.status = 'success'
}

function normalizeStatus(status?: string): AgentFlowStatus {
  if (status === 'failed' || status === 'rejected' || status === 'interrupted') return 'failed'
  if (status === 'success' || status === 'succeeded') return 'success'
  if (status === 'waiting' || status === 'waiting_user') return 'waiting'
  return 'running'
}

function compareEvents(a: Record<string, any>, b: Record<string, any>) {
  const sequenceA = Number(a.sequence || 0)
  const sequenceB = Number(b.sequence || 0)
  if (sequenceA || sequenceB) return sequenceA - sequenceB
  return Number(a._ts || 0) - Number(b._ts || 0)
}
