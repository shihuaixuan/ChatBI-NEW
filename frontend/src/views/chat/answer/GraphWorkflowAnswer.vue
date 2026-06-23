<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import { ChatInfo, type ChatMessage, ChatRecord } from '@/api/chat'
import {
  graphWorkflowApi,
  type GraphPendingInteraction,
  type GraphRunResponse,
} from '@/api/graph-workflow'
import { computed, nextTick, onMounted, ref } from 'vue'
import GraphWorkflowTrace from '@/views/chat/execution-component/GraphWorkflowTrace.vue'
import GraphWorkflowInteractionCard from '@/views/chat/execution-component/GraphWorkflowInteractionCard.vue'
import GraphWorkflowFinalAnswer from '@/views/chat/execution-component/GraphWorkflowFinalAnswer.vue'

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

const internalLoading = ref(false)
const _loading = computed({
  get: () => props.loading || internalLoading.value,
  set: (v) => {
    internalLoading.value = v
    emits('update:loading', v)
  },
})

const traceRefreshKey = ref(0)
const noReasoningName: Array<'sql_answer' | 'chart_answer'> = []
const pendingInteraction = computed<GraphPendingInteraction | undefined>(() => {
  const interaction = props.message?.record?.clarification as GraphPendingInteraction | undefined
  return interaction?.status === 'pending' ? interaction : undefined
})
const waitingInput = computed(() => Boolean(pendingInteraction.value))

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
  currentRecord.trace_id = run.run_id
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
    currentRecord.error = 'Graph Workflow 执行失败，请查看执行详情。'
    emits('error', currentRecord.id)
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

async function sendMessage() {
  _loading.value = true
  if (index.value < 0) {
    _loading.value = false
    return
  }
  const currentRecord: ChatRecord = _currentChat.value.records[index.value]
  const currentDatasetId = datasetId(currentRecord)
  if (!currentDatasetId) {
    currentRecord.error = '当前会话没有可用数据集，无法启动 Graph Workflow。'
    emits('error', currentRecord.id)
    _loading.value = false
    return
  }

  try {
    const run = await graphWorkflowApi.createQuery({
      question: currentRecord.question || '',
      dataset_id: currentDatasetId,
      definition_version: 'v1',
    })
    applyRunToRecord(run, currentRecord)
    traceRefreshKey.value++
  } catch (error) {
    currentRecord.error = `Graph Workflow Error: ${error}`
    emits('error', currentRecord.id)
  } finally {
    _loading.value = false
    emits('scrollBottom')
  }
}

async function submitInteraction(response: Record<string, any>) {
  if (index.value < 0) return
  const currentRecord: ChatRecord = _currentChat.value.records[index.value]
  const interaction = currentRecord.clarification as GraphPendingInteraction | undefined
  const interactionId = interaction?.interaction_id
  if (!currentRecord.trace_id || !interactionId) return
  _loading.value = true
  currentRecord.status = 'running'
  currentRecord.clarification = undefined
  try {
    await graphWorkflowApi.answerInteraction(currentRecord.trace_id, interactionId, response)
    await refreshRun(currentRecord)
  } finally {
    _loading.value = false
  }
}

function skipInteraction() {
  submitInteraction({ skipped: true })
}

function stop() {
  const currentRecord = props.message?.record
  const runId = currentRecord?.trace_id
  if (runId && !currentRecord?.finish) {
    graphWorkflowApi
      .cancel(runId)
      .then((run) => {
        if (currentRecord) {
          currentRecord.status = run.status
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
  if (currentRecord?.trace_id && !currentRecord.finish) {
    refreshRun(currentRecord)
  }
})

defineExpose({ sendMessage, index: () => index.value, stop })
</script>

<template>
  <BaseAnswer v-if="message" :message="message" :reasoning-name="noReasoningName" :loading="_loading">
    <GraphWorkflowTrace
      :run-id="message.record?.trace_id"
      :refresh-key="traceRefreshKey"
      :pending-interaction="pendingInteraction"
    />
    <GraphWorkflowInteractionCard
      :interaction="pendingInteraction"
      :disabled="_loading"
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
