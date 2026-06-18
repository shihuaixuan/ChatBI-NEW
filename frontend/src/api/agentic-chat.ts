import { request } from '@/utils/request'

export interface AgenticClarificationAnswerItem {
  slot: string
  value: string
}

export interface AgenticClarification {
  clarification_id?: number
  target_slots?: string[]
  question?: string
  status?: string
  options?: AgenticClarificationOption[]
}

export interface AgenticClarificationOption {
  label?: string
  value?: string
  slot?: string
  multiple?: boolean
  asset_type?: string
  asset_id?: number
}

export interface AgenticSlotCandidate {
  display_name?: string
  raw_text?: string
  asset_type?: string
  asset_id?: number
  score?: number
  source?: string
}

export interface AgenticSlotIssue {
  slot: string
  raw_text?: string
  reason?: string
  candidates?: AgenticSlotCandidate[]
  sources?: string[]
}

export interface AgenticUnderstandingSummary {
  normalized_question?: string
  intent?: string
  intent_confidence?: number
  slots?: Record<string, any>
  missing_slots?: string[]
  low_confidence_slots?: AgenticSlotIssue[]
  ambiguous_slots?: AgenticSlotIssue[]
  conflict_slots?: AgenticSlotIssue[]
}

export interface AgenticTraceStep {
  index: number
  action: string
  status: string
  tool_name?: string
  strategy?: string
  duration_ms?: number
  summary?: Record<string, any>
  understanding?: AgenticUnderstandingSummary
}

export interface AgenticTraceResponse {
  record_id: number
  run_id?: number
  status?: string
  steps: AgenticTraceStep[]
  events: Array<Record<string, any>>
  clarification?: AgenticClarification
}

export const agenticQuestionApi = {
  add: (data: any, controller?: AbortController) =>
    request.fetchStream('/chat/agentic/question', data, controller),
  clarification: (
    recordId: number,
    answers: AgenticClarificationAnswerItem[],
    controller?: AbortController
  ) =>
    request.fetchStream(
      `/chat/agentic/record/${recordId}/clarification`,
      { answers },
      controller
    ),
  trace: (recordId: number) =>
    request.get<AgenticTraceResponse>(`/chat/agentic/record/${recordId}/trace`),
}
