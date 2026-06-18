<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import { chatApi, ChatInfo, type ChatMessage, ChatRecord } from '@/api/chat.ts'
import {
  agenticQuestionApi,
  type AgenticClarificationAnswerItem,
} from '@/api/agentic-chat'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import ChartBlock from '@/views/chat/chat-block/ChartBlock.vue'
import ClarificationCard from '@/views/chat/clarification/ClarificationCard.vue'
import AgenticTrace from '@/views/chat/execution-component/AgenticTrace.vue'
import JSONBig from 'json-bigint'

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

const _currentChatId = computed({
  get: () => props.currentChatId,
  set: (v) => emits('update:currentChatId', v),
})

const _currentChat = computed({
  get: () => props.currentChat,
  set: (v) => emits('update:currentChat', v),
})

const _loading = computed({
  get: () => props.loading,
  set: (v) => emits('update:loading', v),
})

const stopFlag = ref(false)
const loadingData = ref(false)
const traceRefreshKey = ref(0)

async function consumeStream(response: Response, currentRecord: ChatRecord, controller: AbortController) {
  const reader = response.body?.getReader()
  if (!reader) return
  const decoder = new TextDecoder('utf-8')
  let tempResult = ''

  while (true) {
    if (stopFlag.value) {
      controller.abort()
      break
    }
    const { done, value } = await reader.read()
    if (done) break
    let chunk = decoder.decode(value, { stream: true })
    tempResult += chunk
    const split = tempResult.match(/data:.*}\n\n/g)
    if (!split) continue
    chunk = split.join('')
    tempResult = tempResult.replace(chunk, '')
    for (const str of split) {
      const data = JSONBig.parse(str.replace('data:{', '{'))
      handleEvent(data, currentRecord)
      await nextTick()
      emits('scrollBottom')
    }
  }
}

function handleEvent(data: any, currentRecord: ChatRecord) {
  const payload = data.content || {}
  currentRecord.agentic_trace = currentRecord.agentic_trace || []
  switch (data.type) {
    case 'record-created':
      currentRecord.id = payload.id || payload.record_id
      currentRecord.trace_id = payload.run_id?.toString()
      break
    case 'run-started':
      currentRecord.status = 'running'
      break
    case 'understanding':
    case 'tool-result':
    case 'route-selected':
      currentRecord.agentic_trace.push({ type: data.type, ...payload })
      break
    case 'step-finished':
      if (payload.summary?.sql) currentRecord.sql = payload.summary.sql
      currentRecord.agentic_trace.push({ type: data.type, ...payload })
      traceRefreshKey.value++
      break
    case 'sql-generated':
    case 'sql-validated':
      currentRecord.sql = payload.sql
      currentRecord.agentic_trace.push({ type: data.type, ...payload })
      break
    case 'sql-executed':
      currentRecord.agentic_trace.push({ type: data.type, ...payload })
      getChatData(currentRecord.id)
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
      _loading.value = false
      break
    case 'clarification-accepted':
      currentRecord.status = 'running'
      currentRecord.clarification = undefined
      break
    case 'run-failed':
    case 'error':
      currentRecord.error = payload.content || data.content
      emits('error', currentRecord.id)
      break
    case 'run-finished':
    case 'finish':
      currentRecord.status = 'finished'
      currentRecord.finish = true
      currentRecord.sql_answer = payload.content
      currentRecord.chart_answer = payload.content
      getChatData(currentRecord.id)
      emits('finish', currentRecord.id)
      break
  }
}

async function sendMessage() {
  stopFlag.value = false
  _loading.value = true
  if (index.value < 0 || _currentChatId.value === undefined) {
    _loading.value = false
    return
  }
  const currentRecord: ChatRecord = _currentChat.value.records[index.value]
  const controller = new AbortController()
  try {
    const response = await agenticQuestionApi.add(
      {
        question: currentRecord.question,
        chat_id: _currentChatId.value,
        datasource_id: _currentChat.value.datasource,
      },
      controller
    )
    await consumeStream(response, currentRecord, controller)
  } catch (error) {
    currentRecord.error = `Error:${error}`
    emits('error', currentRecord.id)
  } finally {
    _loading.value = false
  }
}

async function submitClarification(answers: AgenticClarificationAnswerItem[]) {
  if (index.value < 0) return
  const currentRecord: ChatRecord = _currentChat.value.records[index.value]
  if (!currentRecord.id) return
  _loading.value = true
  currentRecord.clarification = undefined
  const controller = new AbortController()
  try {
    const response = await agenticQuestionApi.clarification(currentRecord.id, answers, controller)
    await consumeStream(response, currentRecord, controller)
  } finally {
    _loading.value = false
  }
}

async function restorePendingClarification() {
  const currentRecord = props.message?.record
  if (!currentRecord?.id || currentRecord.status !== 'waiting_user') return
  const trace = await agenticQuestionApi.trace(currentRecord.id)
  currentRecord.agentic_trace = trace.events
  if (trace.clarification?.status === 'pending') {
    currentRecord.clarification = trace.clarification
  }
  traceRefreshKey.value++
}

function getChatData(recordId?: number) {
  if (!recordId) return
  loadingData.value = true
  chatApi
    .get_chart_data(recordId)
    .then((response) => {
      _currentChat.value.records.forEach((record) => {
        if (record.id === recordId) record.data = response
      })
    })
    .finally(() => {
      loadingData.value = false
      emits('scrollBottom')
    })
}

function stop() {
  stopFlag.value = true
  _loading.value = false
  emits('stop')
}

onBeforeUnmount(stop)

onMounted(() => {
  if (props.message?.record?.id && props.message?.record?.finish) {
    getChatData(props.message.record.id)
  }
  restorePendingClarification()
})

defineExpose({ sendMessage, index: () => index.value, stop })
</script>

<template>
  <BaseAnswer v-if="message" :message="message" :reasoning-name="reasoningName" :loading="_loading">
    <ChartBlock
      style="margin-top: 6px"
      :message="message"
      :record-id="recordId"
      :loading-data="loadingData"
    />
    <ClarificationCard
      :clarification="message.record?.clarification"
      :disabled="_loading"
      @submit="submitClarification"
    />
    <AgenticTrace :record-id="message.record?.id" :refresh-key="traceRefreshKey" />
    <slot></slot>
    <template #tool>
      <slot name="tool"></slot>
    </template>
    <template #footer>
      <slot name="footer"></slot>
    </template>
  </BaseAnswer>
</template>
