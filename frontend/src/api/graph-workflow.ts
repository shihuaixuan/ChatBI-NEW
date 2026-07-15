import { request } from '@/utils/request'
import { useAssistantStore } from '@/stores/assistant'
import { useCache } from '@/utils/useCache'

const { wsCache } = useCache()
const assistantStore = useAssistantStore()

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
  label?: string
  status: string
  prompt?: string
  options?: Array<Record<string, any>>
  response_schema?: Record<string, any>
  allowed_update_paths?: string[]
}

export interface GraphRunResponse {
  run_id: string
  // 交互式 Graph Run 返回服务端生成的真实聊天记录 ID。
  record_id?: number
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
  label?: string
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

export interface GraphEventResponse {
  event_id?: string
  sequence?: number
  event_type: string
  node_name?: string | null
  public_payload?: Record<string, any>
  created_at?: string
}

export interface GraphControlResponse {
  run_id: string
  status: string
}

export interface GraphStreamHandlers {
  onEvent?: (event: GraphEventResponse) => void
  onError?: (error: unknown) => void
  signal?: AbortSignal
}

export const graphWorkflowApi = {
  createQuery: (data: GraphQueryRequest) =>
    request.post<GraphRunResponse>('/graph/queries', {
      definition_version: 'v1',
      ...data,
    }),
  getRun: (runId: string) => request.get<GraphRunResponse>(`/graph/runs/${runId}`),
  events: (runId: string, afterSequence = 0) =>
    request.get<{ events: GraphEventResponse[] }>(
      `/graph/runs/${runId}/events?after_sequence=${afterSequence}`
    ),
  trace: (runId: string) => request.get<GraphTraceResponse>(`/graph/runs/${runId}/trace`),
  answerInteraction: (runId: string, interactionId: string, response: Record<string, any>) =>
    request.post<GraphControlResponse>(
      `/graph/runs/${runId}/interactions/${interactionId}/responses`,
      { response }
    ),
  streamInteraction: (
    runId: string,
    interactionId: string,
    response: Record<string, any>,
    afterSequence: number,
    handlers: GraphStreamHandlers = {}
  ) =>
    streamGraphSse(
      `/graph/runs/${runId}/interactions/${interactionId}/responses/stream?after_sequence=${afterSequence}`,
      {
        method: 'POST',
        body: JSON.stringify({ response }),
        handlers,
      }
    ),
  cancel: (runId: string) => request.post<GraphControlResponse>(`/graph/runs/${runId}/cancel`, {}),
  retry: (runId: string) => request.post<GraphControlResponse>(`/graph/runs/${runId}/retry`, {}),
  streamQuery: (data: GraphQueryRequest, handlers: GraphStreamHandlers = {}) =>
    streamGraphSse('/graph/queries/stream', {
      method: 'POST',
      body: JSON.stringify({
        definition_version: 'v1',
        ...data,
      }),
      handlers,
    }),
  // 新问题必须通过 chat-scoped 入口建立稳定的 Run/ChatRecord 归属。
  streamChatQuery: (
    chatId: number,
    data: GraphQueryRequest,
    handlers: GraphStreamHandlers = {}
  ) =>
    streamGraphSse(`/graph/chats/${chatId}/queries/stream`, {
      method: 'POST',
      body: JSON.stringify({ definition_version: 'v1', ...data }),
      handlers,
    }),
  streamEvents: (runId: string, afterSequence: number, handlers: GraphStreamHandlers = {}) =>
    streamGraphSse(`/graph/runs/${runId}/events/stream?after_sequence=${afterSequence}`, {
      method: 'GET',
      handlers,
    }),
}

async function streamGraphSse(
  url: string,
  options: {
    method: 'GET' | 'POST'
    body?: string
    handlers: GraphStreamHandlers
  }
) {
  try {
    const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}${url}`, {
      method: options.method,
      headers: await graphStreamHeaders(url),
      body: options.body,
      signal: options.handlers.signal,
    })
    if (!response.ok || !response.body) {
      throw new Error(`Graph Workflow SSE Error: ${response.status}`)
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const frames = buffer.split('\n\n')
      buffer = frames.pop() || ''
      for (const frame of frames) {
        const event = parseSseFrame(frame)
        if (event) options.handlers.onEvent?.(event)
      }
    }
    if (buffer.trim()) {
      const event = parseSseFrame(buffer)
      if (event) options.handlers.onEvent?.(event)
    }
  } catch (error) {
    if ((error as Error)?.name !== 'AbortError') options.handlers.onError?.(error)
    throw error
  }
}

async function graphStreamHeaders(url: string) {
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  }
  const token = wsCache.get('user.token')
  if (token) headers['X-SQLBOT-TOKEN'] = `Bearer ${token}`
  if (assistantStore.getToken) {
    const prefix = assistantStore.getType === 4 ? 'Embedded ' : 'Assistant '
    headers['X-SQLBOT-ASSISTANT-TOKEN'] = `${prefix}${assistantStore.getToken}`
    delete headers['X-SQLBOT-TOKEN']
    if (
      assistantStore.getType &&
      !!(assistantStore.getType % 2) &&
      assistantStore.getCertificate
    ) {
      await assistantStore.refreshCertificate(url)
      headers['X-SQLBOT-ASSISTANT-CERTIFICATE'] = btoa(
        encodeURIComponent(assistantStore.getCertificate)
      )
    }
    if (assistantStore.getHostOrigin) headers['X-SQLBOT-HOST-ORIGIN'] = assistantStore.getHostOrigin
    if (!assistantStore.getType || assistantStore.getType === 2) {
      headers['X-SQLBOT-ASSISTANT-ONLINE'] = String(assistantStore.getOnline)
    }
  }
  return headers
}

function parseSseFrame(frame: string): GraphEventResponse | null {
  const dataLines = frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.replace(/^data:\s?/, ''))
  if (!dataLines.length) return null
  try {
    return JSON.parse(dataLines.join('\n')) as GraphEventResponse
  } catch (error) {
    console.error(error)
    return null
  }
}
