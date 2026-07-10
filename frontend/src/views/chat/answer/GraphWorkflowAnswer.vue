<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import { ChatInfo, type ChatMessage, ChatRecord } from '@/api/chat'
import {
  graphWorkflowApi,
  type GraphEventResponse,
  type GraphPendingInteraction,
  type GraphRunResponse,
} from '@/api/graph-workflow'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import GraphWorkflowTrace from '@/views/chat/execution-component/GraphWorkflowTrace.vue'
import GraphWorkflowInteractionCard from '@/views/chat/execution-component/GraphWorkflowInteractionCard.vue'
import GraphWorkflowFinalAnswer from '@/views/chat/execution-component/GraphWorkflowFinalAnswer.vue'
import {
  graphInteractionControlsDisabled,
  graphLatestEventSequence,
  pendingInteractionFromGraphEvent,
} from '@/views/chat/execution-component/graphWorkflowDisplay'

const props = withDefaults(
  defineProps<{
    recordId?: number
    chatList?: Array<ChatInfo>
    currentChatId?: number
    currentChat?: ChatInfo
    message?: ChatMessage
    loading?: boolean
    reasoningName: 'sql_answer' | 'chart_answer' | Array<'sql_answer' | 'chart_answer'>
  }>(),
  {
    recordId: undefined,
    chatList: () => [],
    currentChatId: undefined,
    currentChat: () => new ChatInfo(),
    message: undefined,
    loading: false,
  }
)

const emits = defineEmits([
  'finish',
  'error',
  'stop',
  'scrollBottom',
  'update:loading',
  'update:chatList',
  'update:currentChat',
  'update:currentChatId',
])

const index = computed(() => {
  if (props.message?.index) return props.message.index
  if (props.message?.index === 0) return 0
  return -1
})

const _currentChat = computed({
  get: () => props.currentChat,
  set: (v) => emits('update:currentChat', v),
})
const _currentChatId = computed(() => Number(props.currentChatId || 0))

const internalLoading = ref(false)
const _loading = computed({
  get: () => props.loading || internalLoading.value,
  set: (v) => {
    internalLoading.value = v
    emits('update:loading', v)
  },
})

const traceRefreshKey = ref(0)
const liveEvents = ref<GraphEventResponse[]>([])
const pendingLiveEvents: GraphEventResponse[] = []
const processingLiveEvents = ref(false)
const streamController = ref<AbortController>()
const submittingInteraction = ref(false)
const answeredInteractionIds = new Set<string>()
const noReasoningName: Array<'sql_answer' | 'chart_answer'> = []
const pendingInteraction = computed<GraphPendingInteraction | undefined>(() => {
  const interaction = props.message?.record?.clarification as GraphPendingInteraction | undefined
  return interaction?.status === 'pending' ? interaction : undefined
})
const waitingInput = computed(() => Boolean(pendingInteraction.value))
const interactionDisabled = computed(
  () =>
    submittingInteraction.value ||
    graphInteractionControlsDisabled(_loading.value, waitingInput.value)
)
const lastEventSequence = computed(() =>
  graphLatestEventSequence(liveEvents.value, pendingLiveEvents)
)

function datasetId(currentRecord: ChatRecord) {
  return Number(
    _currentChat.value.dataset_id ||
      currentRecord.dataset_id ||
      _currentChat.value.datasource ||
      currentRecord.datasource ||
      0
  )
}

function applyRunToRecord(run: GraphRunResponse, currentRecord: ChatRecord) {
  // 临时前端记录在首次 Run 查询后替换为服务端生成的真实记录 ID。
  if (run.record_id) currentRecord.id = run.record_id
  currentRecord.trace_id = run.run_id
  currentRecord.execution_type = 'graph'
  currentRecord.status = run.status
  currentRecord.agentic_trace = run.context_summary?.variables
  currentRecord.clarification =
    run.context_summary?.pending_interaction?.status === 'pending'
      ? run.context_summary.pending_interaction
      : undefined

  const variables = run.context_summary?.variables || {}
  const finalReply = variables.final_reply || run.output?.final_reply
  const answer =
    finalReply?.final_answer ||
    finalReply?.answer ||
    finalReply?.content ||
    variables.answer?.answer ||
    variables.answer?.final_answer ||
    run.output?.answer
  if (answer) {
    currentRecord.sql_answer = answer
    currentRecord.chart_answer = answer
  }

  const sql = run.context_summary?.variables?.sql
  if (sql?.sql) currentRecord.sql = sql.sql

  if (currentRecord.clarification || run.status === 'waiting_input') {
    currentRecord.status = 'waiting_input'
    currentRecord.finish = false
    _loading.value = false
    emits('stop')
    return
  }
  if (run.status === 'succeeded') {
    currentRecord.finish = true
    emits('finish', currentRecord.id)
  }
  if (run.status === 'failed') {
    currentRecord.finish = true
    currentRecord.error = 'Graph Workflow 执行失败，请查看执行详情。'
    emits('error', currentRecord.id)
  }
  if (run.status === 'cancelled') {
    currentRecord.finish = true
    currentRecord.clarification = undefined
    emits('stop')
  }
}

async function refreshRun(currentRecord: ChatRecord) {
  if (!currentRecord.trace_id) return
  const run = await graphWorkflowApi.getRun(currentRecord.trace_id)
  applyRunToRecord(run, currentRecord)
  traceRefreshKey.value++
  await nextTick()
  emits('scrollBottom')
}

function nextRunId() {
  return `graph-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function resetStream() {
  streamController.value?.abort()
  streamController.value = undefined
}

function appendLiveEvent(event: GraphEventResponse) {
  if (event.sequence && liveEvents.value.some((item) => item.sequence === event.sequence)) return
  liveEvents.value.push(event)
  liveEvents.value.sort((a, b) => Number(a.sequence || 0) - Number(b.sequence || 0))
}

function queueLiveEvent(event: GraphEventResponse) {
  pendingLiveEvents.push(event)
  applyPendingInteractionFromEvent(event)
  processLiveEvents()
}

function queueOptimisticResumeEvent(interaction: GraphPendingInteraction, afterSequence: number) {
  queueLiveEvent({
    sequence: afterSequence + 0.001,
    event_type: 'run.resumed',
    node_name: interaction.node_name,
    public_payload: {
      synthetic: true,
      interaction_id: interaction.interaction_id,
      node_name: interaction.node_name,
    },
  })
}

function applyPendingInteractionFromEvent(event: GraphEventResponse) {
  const interaction = pendingInteractionFromGraphEvent(event) as GraphPendingInteraction | undefined
  if (!interaction || index.value < 0) return
  if (answeredInteractionIds.has(interaction.interaction_id)) return
  const currentRecord: ChatRecord | undefined = _currentChat.value.records[index.value]
  if (!currentRecord) return
  currentRecord.clarification = interaction
  currentRecord.status = 'waiting_input'
  currentRecord.finish = false
  _loading.value = false
  emits('stop')
  nextTick(() => emits('scrollBottom'))
}

async function processLiveEvents() {
  if (processingLiveEvents.value) return
  processingLiveEvents.value = true
  while (pendingLiveEvents.length) {
    const event = pendingLiveEvents.shift()
    if (!event) continue
    appendLiveEvent(event)
    await nextTick()
    if (event.event_type === 'node.succeeded') {
      await new Promise((resolve) => setTimeout(resolve, 260))
    }
  }
  processingLiveEvents.value = false
}

async function waitLiveEventQueue() {
  while (processingLiveEvents.value || pendingLiveEvents.length) {
    await new Promise((resolve) => setTimeout(resolve, 30))
  }
}

async function streamRunToBoundary(currentRecord: ChatRecord, stream: () => Promise<void>) {
  resetStream()
  streamController.value = new AbortController()
  _loading.value = true
  try {
    await stream()
    await waitLiveEventQueue()
    await refreshRun(currentRecord)
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') return
    currentRecord.error = `Graph Workflow SSE Error: ${error}`
    emits('error', currentRecord.id)
  } finally {
    _loading.value = false
    emits('scrollBottom')
  }
}

async function sendMessage() {
  if (index.value < 0) {
    _loading.value = false
    return
  }
  const currentRecord: ChatRecord = _currentChat.value.records[index.value]
  if (!_currentChatId.value) {
    currentRecord.error = '当前会话 ID 无效，无法启动 Graph Workflow。'
    emits('error', currentRecord.id)
    _loading.value = false
    return
  }
  const currentDatasetId = datasetId(currentRecord)
  if (!currentDatasetId) {
    currentRecord.error = '当前会话没有可用数据集，无法启动 Graph Workflow。'
    emits('error', currentRecord.id)
    _loading.value = false
    return
  }

  liveEvents.value = []
  pendingLiveEvents.splice(0)
  answeredInteractionIds.clear()
  currentRecord.trace_id = nextRunId()
  currentRecord.status = 'running'
  currentRecord.clarification = undefined
  currentRecord.finish = false
  await streamRunToBoundary(currentRecord, () =>
    graphWorkflowApi.streamChatQuery(
      _currentChatId.value,
      {
        run_id: currentRecord.trace_id,
        question: currentRecord.question || '',
        dataset_id: currentDatasetId,
        definition_version: 'v1',
      },
      {
        signal: streamController.value?.signal,
        onEvent: queueLiveEvent,
      }
    )
  )
}

async function submitInteraction(response: Record<string, any>) {
  if (submittingInteraction.value) return
  if (index.value < 0) return
  const currentRecord: ChatRecord = _currentChat.value.records[index.value]
  const interaction = currentRecord.clarification as GraphPendingInteraction | undefined
  const interactionId = interaction?.interaction_id
  if (!currentRecord.trace_id || !interactionId) return
  submittingInteraction.value = true
  answeredInteractionIds.add(interactionId)
  const afterSequence = lastEventSequence.value
  currentRecord.status = 'running'
  currentRecord.clarification = undefined
  queueOptimisticResumeEvent(interaction, afterSequence)
  try {
    await streamRunToBoundary(currentRecord, () =>
      graphWorkflowApi.streamInteraction(
        currentRecord.trace_id || '',
        interactionId,
        response,
        afterSequence,
        {
          signal: streamController.value?.signal,
          onEvent: queueLiveEvent,
        }
      )
    )
  } finally {
    submittingInteraction.value = false
  }
}

function skipInteraction() {
  submitInteraction({ skipped: true })
}

function stop() {
  resetStream()
  const currentRecord = props.message?.record
  const runId = currentRecord?.trace_id
  if (runId && !currentRecord?.finish) {
    graphWorkflowApi
      .cancel(runId)
      .then((run) => {
        if (currentRecord) {
          currentRecord.status = run.status
          currentRecord.finish = run.status === 'cancelled'
          currentRecord.clarification = undefined
          traceRefreshKey.value++
        }
      })
      .catch((error) => {
        console.error(error)
      })
  }
  _loading.value = false
  emits('stop')
}

onMounted(() => {
  const currentRecord = props.message?.record
  // 终态历史直接展示 ChatRecord 快照，只恢复仍在运行或等待输入的 Run。
  if (
    currentRecord?.trace_id &&
    ['running', 'waiting_input'].includes(currentRecord.status || '')
  ) {
    refreshRun(currentRecord)
  }
})

onBeforeUnmount(resetStream)

defineExpose({ sendMessage, index: () => index.value, stop })
</script>

<template>
  <BaseAnswer
    v-if="message"
    :message="message"
    :reasoning-name="noReasoningName"
    :loading="_loading"
  >
    <GraphWorkflowTrace
      :run-id="message.record?.trace_id"
      :refresh-key="traceRefreshKey"
      :events="liveEvents"
      :pending-interaction="pendingInteraction"
      :runtime-loading="_loading"
      @refresh-run="message.record && refreshRun(message.record)"
    />
    <GraphWorkflowInteractionCard
      :interaction="pendingInteraction"
      :disabled="interactionDisabled"
      @submit="submitInteraction"
      @skip="skipInteraction"
    />
    <GraphWorkflowFinalAnswer
      :record="message.record"
      :loading="_loading"
      :waiting-input="waitingInput"
    />
    <slot></slot>
    <template #tool>
      <slot name="tool"></slot>
    </template>
    <template #footer>
      <slot name="footer"></slot>
    </template>
  </BaseAnswer>
</template>
