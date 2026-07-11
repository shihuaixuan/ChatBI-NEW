import { request } from '@/utils/request'

export interface AgentClarificationOption {
  label?: string
  value?: string
  asset_id?: number
}

export interface AgentClarification {
  clarification_id?: number
  question?: string
  status?: string
  options?: AgentClarificationOption[]
}

export interface AgentClarificationAnswer {
  selections: Array<{ label?: string; value?: string }>
  text?: string
}

export interface AgentTraceStep {
  index: number
  tool_name?: string
  status: string
  latency_ms?: number
  args_summary?: Record<string, any>
  result_summary?: Record<string, any>
  error?: string
}

export interface AgentTraceResponse {
  record_id: number
  run_id?: number
  status?: string
  error_class?: string
  budget?: Record<string, any>
  steps: AgentTraceStep[]
  events: Array<Record<string, any>>
  clarification?: AgentClarification
}

export const agentQuestionApi = {
  add: (data: any, controller?: AbortController) =>
    request.fetchStream('/chat/agent/question', data, controller),
  clarification: (recordId: number, answer: AgentClarificationAnswer, controller?: AbortController) =>
    request.fetchStream(`/chat/agent/record/${recordId}/clarification`, answer, controller),
  trace: (recordId: number) => request.get<AgentTraceResponse>(`/chat/agent/record/${recordId}/trace`),
  events: (runId: number, afterSequence: number = 0) =>
    request.get(`/chat/agent/runs/${runId}/events?after_sequence=${afterSequence}`),
  cancel: (runId: number) => request.post(`/chat/agent/runs/${runId}/cancel`),
}
