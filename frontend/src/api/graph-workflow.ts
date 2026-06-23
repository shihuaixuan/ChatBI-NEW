import { request } from '@/utils/request'

export interface GraphQueryRequest {
  question: string
  dataset_id: number
  definition_version?: 'minimal-v1' | 'v1'
  request_id?: string
  run_id?: string
}

export interface GraphPendingInteraction {
  interaction_id: string
  run_id: string
  node_name: string
  status: string
  prompt?: string
  options?: Array<Record<string, any>>
  response_schema?: Record<string, any>
  allowed_update_paths?: string[]
}

export interface GraphRunResponse {
  run_id: string
  status: string
  current_node?: string
  output?: Record<string, any>
  context_summary?: {
    question?: string
    dataset_id?: number
    variables?: Record<string, any>
    pending_interaction?: GraphPendingInteraction | null
  }
}

export interface GraphTraceNode {
  name: string
  status: string
  route_reason?: string
  output?: any
}

export interface GraphTraceResponse {
  run_id: string
  status: string
  current_node?: string
  nodes: GraphTraceNode[]
}

export interface GraphControlResponse {
  run_id: string
  status: string
}

export const graphWorkflowApi = {
  createQuery: (data: GraphQueryRequest) =>
    request.post<GraphRunResponse>('/graph/queries', {
      definition_version: 'v1',
      ...data,
    }),
  getRun: (runId: string) => request.get<GraphRunResponse>(`/graph/runs/${runId}`),
  trace: (runId: string) => request.get<GraphTraceResponse>(`/graph/runs/${runId}/trace`),
  answerInteraction: (runId: string, interactionId: string, response: Record<string, any>) =>
    request.post<GraphControlResponse>(
      `/graph/runs/${runId}/interactions/${interactionId}/responses`,
      { response }
    ),
  cancel: (runId: string) => request.post<GraphControlResponse>(`/graph/runs/${runId}/cancel`, {}),
  retry: (runId: string) => request.post<GraphControlResponse>(`/graph/runs/${runId}/retry`, {}),
}
