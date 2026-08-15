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

export type AgentStreamRequest =
  | {
      action: 'start'
      chat_id: number
      question: string
      datasource_id?: number
    }
  | {
      action: 'resume'
      record_id: number
      clarification: AgentClarificationAnswer
    }

export interface AgentTimelineStep {
  id?: number
  index: number
  status: string
  latency_ms?: number
  result_summary?: Record<string, any>
  error?: string
}

export interface AgentTimelineToolCall {
  tool_call_id: string
  step_id: number
  tool_name: string
  status: string
  latency_ms?: number
  args_summary?: Record<string, any>
  result_summary?: Record<string, any>
  error_code?: string
}

export interface AgentTimelineResponse {
  record_id: number
  run_id?: number
  status?: string
  error_class?: string
  budget?: Record<string, any>
  steps: AgentTimelineStep[]
  tool_calls?: AgentTimelineToolCall[]
  events: Array<Record<string, any>>
  clarification?: AgentClarification
}

export interface AgentCancelResponse {
  run_id: number
  status: string
  cancel_requested_at?: string
  cancelled_at?: string
  cancel_stage?: string
}

export interface AgentTraceOverview {
  run_id: number
  record_id: number
  status: string
  started_at?: string
  finished_at?: string
  duration_ms: number
  total_tokens: number
  input_tokens: number
  output_tokens: number
  node_count: number
  llm_call_count: number
  tool_call_count: number
  invocation_count: number
  recovery_count: number
  failed_node_count: number
  waiting_node_count: number
  last_sequence: number
  partial: boolean
  trace_complete: boolean
}

export interface AgentTraceNode {
  id: number
  parent_id: number | null
  node_key: string
  node_type: string
  name: string
  display_name: string
  status: string
  sequence: number
  started_at: string
  finished_at?: string | null
  latency_ms?: number | null
  token_usage: Record<string, unknown>
  input_summary: Record<string, unknown>
  output_summary: Record<string, unknown>
  metadata: Record<string, unknown>
  error_code?: string | null
  error_category?: string | null
  error?: string | null
  has_input_detail: boolean
  has_output_detail: boolean
}

export interface AgentTraceResponse {
  available: boolean
  unavailable_reason?: string | null
  detail_access: 'allowed' | 'summary_only'
  overview: AgentTraceOverview
  nodes: AgentTraceNode[]
}

export interface AgentTraceNodeDetail {
  node: AgentTraceNode
  input_summary: Record<string, unknown>
  output_summary: Record<string, unknown>
  input_detail?: Record<string, unknown> | null
  output_detail?: Record<string, unknown> | null
  state_diff: Record<string, unknown>
  metadata: Record<string, unknown>
  trace_id?: string | null
  span_id?: string | null
}

export const agentQuestionApi = {
  stream: (data: AgentStreamRequest, controller?: AbortController) =>
    request.fetchStream('/chat/agent/stream', data, controller),
  timeline: (recordId: number) =>
    request.get<AgentTimelineResponse>(`/chat/agent/record/${recordId}/timeline`),
  trace: (recordId: number) =>
    request.get<AgentTraceResponse>(`/chat/agent/record/${recordId}/trace`),
  traceNode: (recordId: number, nodeId: number) =>
    request.get<AgentTraceNodeDetail>(`/chat/agent/record/${recordId}/trace/nodes/${nodeId}`),
  events: (runId: number, afterSequence: number = 0) =>
    request.get(`/chat/agent/runs/${runId}/events?after_sequence=${afterSequence}`),
  cancel: (runId: number) =>
    request.post<AgentCancelResponse>(`/chat/agent/runs/${runId}/cancel`),
}
