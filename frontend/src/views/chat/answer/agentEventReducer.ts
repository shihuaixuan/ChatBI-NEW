import type { ChatRecord } from '@/api/chat'

export type AgentEventPhase = 'start' | 'delta' | 'end' | 'snapshot' | 'error'

export interface AgentRenderEvent {
  type?: string
  kind?: string
  phase?: AgentEventPhase
  domain?: string
  block_id?: string
  content?: unknown
  record_id?: number
  run_id?: number
  sequence?: number
  [key: string]: any
}

export interface AgentEventEffect {
  terminal?: 'finished' | 'failed'
  waitingUser?: boolean
}

const LEGACY_CONTRACT: Record<string, Pick<AgentRenderEvent, 'kind' | 'phase' | 'domain'>> = {
  'record-created': { kind: 'run', phase: 'start', domain: 'run' },
  'run-started': { kind: 'run', phase: 'start', domain: 'run' },
  'step-started': { kind: 'run', phase: 'start', domain: 'run' },
  'run-finished': { kind: 'run', phase: 'end', domain: 'run' },
  finish: { kind: 'run', phase: 'end', domain: 'run' },
  'run-failed': { kind: 'run', phase: 'error', domain: 'run' },
  error: { kind: 'run', phase: 'error', domain: 'run' },
  'question-understood': { kind: 'thinking', phase: 'end', domain: 'reasoning' },
  thinking: { kind: 'thinking', phase: 'snapshot', domain: 'reasoning' },
  answer: { kind: 'text', phase: 'end', domain: 'answer' },
  'tool-called': { kind: 'tool', phase: 'start', domain: 'tool' },
  'workflow-step': { kind: 'tool', phase: 'start', domain: 'tool' },
  'tool-result': { kind: 'tool', phase: 'end', domain: 'tool' },
  'sql-generated': { kind: 'artifact', phase: 'end', domain: 'artifact' },
  'sql-validated': { kind: 'artifact', phase: 'end', domain: 'artifact' },
  'sql-executed': { kind: 'artifact', phase: 'end', domain: 'artifact' },
  'chart-generated': { kind: 'artifact', phase: 'end', domain: 'artifact' },
  clarification: { kind: 'interaction', phase: 'start', domain: 'interaction' },
  'clarification-accepted': { kind: 'interaction', phase: 'end', domain: 'interaction' },
}

export function normalizeAgentEvent(raw: AgentRenderEvent): AgentRenderEvent {
  const payload = isObject(raw.content) ? raw.content : {}
  const legacy = raw.type ? LEGACY_CONTRACT[raw.type] : undefined
  const event = {
    ...payload,
    ...raw,
    kind: raw.kind || legacy?.kind,
    phase: raw.phase || legacy?.phase,
    domain: raw.domain || legacy?.domain,
  }
  event.type = eventTypeFromContract(event) || raw.type
  return event
}

export function reduceAgentEvent(
  currentRecord: ChatRecord,
  raw: AgentRenderEvent
): AgentEventEffect {
  const event = normalizeAgentEvent(raw)
  const payload = isObject(raw.content) ? raw.content : event
  const executionEvents =
    currentRecord.execution_events || currentRecord.execution_trace || []
  currentRecord.execution_events = executionEvents
  currentRecord.execution_trace = executionEvents
  executionEvents.push({ ...event, ...payload, _ts: Date.now() })

  switch (event.type) {
    case 'record-created':
      currentRecord.id = payload.id || payload.record_id || event.record_id
      currentRecord.run_id = String(payload.run_id || event.run_id || '') || undefined
      currentRecord.trace_id = currentRecord.run_id
      break
    case 'run-started':
      currentRecord.status = 'running'
      break
    case 'sql-generated':
    case 'sql-validated':
      currentRecord.sql = payload.sql
      break
    case 'chart-generated':
      currentRecord.chart = JSON.stringify(payload.chart || {})
      break
    case 'answer':
      currentRecord.sql_answer = payload.content
      currentRecord.chart_answer = payload.content
      break
    case 'clarification':
      currentRecord.status = 'waiting_user'
      currentRecord.clarification = payload
      return { waitingUser: true }
    case 'clarification-accepted':
      currentRecord.status = 'running'
      currentRecord.clarification = undefined
      break
    case 'run-failed':
    case 'error': {
      const alreadyFailed = currentRecord.status === 'failed'
      currentRecord.status = 'failed'
      currentRecord.error = payload.content || raw.content
      return alreadyFailed ? {} : { terminal: 'failed' }
    }
    case 'run-finished':
    case 'finish': {
      const alreadyFinished = currentRecord.finish === true
      currentRecord.status = 'finished'
      currentRecord.finish = true
      currentRecord.sql_answer = payload.content
      currentRecord.chart_answer = payload.content
      return alreadyFinished ? {} : { terminal: 'finished' }
    }
  }
  return {}
}

export function latestAgentEventSequence(currentRecord: ChatRecord) {
  const events = Array.isArray(currentRecord.execution_events)
    ? currentRecord.execution_events
    : Array.isArray(currentRecord.execution_trace)
      ? currentRecord.execution_trace
      : []
  return events.reduce((max, event: AgentRenderEvent) => Math.max(max, Number(event.sequence || 0)), 0)
}

function eventTypeFromContract(event: AgentRenderEvent) {
  if (!event.kind || !event.phase || !event.domain) return event.type
  if (event.domain === 'run' && event.phase === 'error') return 'run-failed'
  if (event.domain === 'run' && event.phase === 'end') return 'run-finished'
  if (event.kind === 'text' && event.phase === 'end') return 'answer'
  if (event.kind === 'interaction' && event.phase === 'start') return 'clarification'
  if (event.kind === 'interaction' && event.phase === 'end') return 'clarification-accepted'
  if (event.kind === 'thinking' && ['delta', 'snapshot'].includes(event.phase)) return 'thinking'
  if (event.kind === 'thinking' && event.phase === 'end') return 'question-understood'
  if (event.kind === 'tool' && event.phase === 'end') return 'tool-result'
  if (event.kind === 'tool' && event.phase === 'start') {
    return event.action ? 'workflow-step' : 'tool-called'
  }
  return event.type
}

function isObject(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
