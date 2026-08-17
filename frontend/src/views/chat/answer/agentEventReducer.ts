import type { ChatRecord } from '@/api/chat'

export type AgentEventPhase = 'start' | 'delta' | 'end' | 'snapshot' | 'error'

export interface AgentRenderEvent {
  kind: string
  phase: AgentEventPhase
  domain: string
  block_id?: string
  content?: any
  record_id?: number
  run_id?: number
  sequence?: number
  [key: string]: any
}

export interface AgentEventEffect {
  terminal?: 'finished' | 'failed' | 'cancelled'
  waitingUser?: boolean
}

export function normalizeAgentEvent(raw: Record<string, any>): AgentRenderEvent {
  if (!raw.kind || !raw.phase || !raw.domain) {
    throw new Error('Agent event contract requires kind, phase and domain')
  }
  const payload = isObject(raw.content) ? raw.content : {}
  const eventContent = isObject(raw.content) ? payload.content : raw.content
  return {
    ...raw,
    ...payload,
    content: eventContent,
  } as AgentRenderEvent
}

export function reduceAgentEvent(
  currentRecord: ChatRecord,
  raw: AgentRenderEvent
): AgentEventEffect {
  const event = normalizeAgentEvent(raw)
  const executionEvents = currentRecord.execution_events || []
  currentRecord.execution_events = executionEvents
  const duplicate =
    event.sequence !== undefined &&
    executionEvents.some(
      (item: AgentRenderEvent) =>
        item.run_id === event.run_id && Number(item.sequence) === Number(event.sequence)
    )
  if (duplicate) return {}
  executionEvents.push({ ...event, _ts: Date.now() })

  switch (event.domain) {
    case 'run.created':
      currentRecord.id = event.id || event.record_id
      currentRecord.run_id = String(event.run_id || '') || undefined
      break
    case 'run.started':
      currentRecord.status = 'running'
      break
    case 'run.cancel-requested':
      currentRecord.status = 'cancelling'
      break
    case 'sql.generated':
    case 'sql.validated':
      currentRecord.sql = event.sql
      break
    case 'chart.generated':
      currentRecord.chart = JSON.stringify(event.chart || {})
      break
    case 'answer.completed':
      currentRecord.sql_answer = event.answer ?? event.content
      currentRecord.chart_answer = event.answer ?? event.content
      if (event.chart) currentRecord.chart = JSON.stringify(event.chart)
      currentRecord.claims = Array.isArray(event.claims) ? event.claims : []
      currentRecord.caliber_card = isObject(event.caliber_card) ? event.caliber_card : {}
      currentRecord.chart_spec = isObject(event.chart_spec) ? event.chart_spec : {}
      break
    case 'clarification.required':
      currentRecord.status = 'waiting_user'
      currentRecord.clarification = event
      return { waitingUser: true }
    case 'clarification.accepted':
      currentRecord.status = 'running'
      currentRecord.clarification = undefined
      break
    case 'run.failed': {
      const alreadyFailed = currentRecord.status === 'failed'
      currentRecord.status = 'failed'
      currentRecord.error = event.content
      return alreadyFailed ? {} : { terminal: 'failed' }
    }
    case 'run.cancelled': {
      const alreadyCancelled = currentRecord.status === 'cancelled'
      currentRecord.status = 'cancelled'
      currentRecord.error = event.content
      return alreadyCancelled ? {} : { terminal: 'cancelled' }
    }
    case 'run.finished': {
      const alreadyFinished = currentRecord.finish === true
      currentRecord.status = 'finished'
      currentRecord.finish = true
      // run.finished 是最终一致性事件，同时携带回答和图表，避免只收到其中一部分。
      currentRecord.sql_answer = event.answer ?? event.content
      currentRecord.chart_answer = event.answer ?? event.content
      if (event.chart) currentRecord.chart = JSON.stringify(event.chart)
      currentRecord.claims = Array.isArray(event.claims) ? event.claims : []
      currentRecord.caliber_card = isObject(event.caliber_card) ? event.caliber_card : {}
      currentRecord.chart_spec = isObject(event.chart_spec) ? event.chart_spec : {}
      return alreadyFinished ? {} : { terminal: 'finished' }
    }
  }
  return {}
}

export function latestAgentEventSequence(currentRecord: ChatRecord) {
  const events = Array.isArray(currentRecord.execution_events) ? currentRecord.execution_events : []
  return events.reduce(
    (max, event: AgentRenderEvent) => Math.max(max, Number(event.sequence || 0)),
    0
  )
}

function isObject(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
