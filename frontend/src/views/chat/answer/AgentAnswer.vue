<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import { chatApi, ChatInfo, type ChatMessage, ChatRecord } from '@/api/chat.ts'
import { agentQuestionApi, type AgentClarificationAnswer } from '@/api/agent-chat'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import ChartBlock from '@/views/chat/chat-block/ChartBlock.vue'
import AgentTimeline from '@/views/chat/execution-component/AgentTimeline.vue'
import MdComponent from '@/views/chat/component/MdComponent.vue'
import JSONBig from 'json-bigint'
import {
  latestAgentEventSequence,
  reduceAgentEvent,
} from '@/views/chat/answer/agentEventReducer'

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
// 全局 loading 控制输入区，执行流运行态只属于当前这条消息，不能污染历史记录。
const runtimeLoading = ref(false)

// Agent 的最终回答与图表配置分开生成，答案文本直接使用后端持久化的 Markdown。
const finalAnswer = computed(
  () => props.message?.record?.sql_answer || props.message?.record?.chart_answer || ''
)

async function pushOptimisticClarificationAccepted(currentRecord: ChatRecord) {
  // 用户已提交澄清后，先让时间线退出等待态；真实后端事件随后会补齐持久化序号。
  reduceAgentEvent(currentRecord, {
    type: 'clarification-accepted',
    kind: 'interaction',
    phase: 'end',
    domain: 'interaction',
    sequence: latestAgentEventSequence(currentRecord) + 0.001,
    content: {
      record_id: currentRecord.id,
      run_id: currentRecord.run_id,
      synthetic: true,
    },
  })
  await nextTick()
  emits('scrollBottom')
}

async function consumeStream(
  response: Response,
  currentRecord: ChatRecord,
  controller: AbortController
) {
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
      try {
        await nextTick()
        emits('scrollBottom')
      } catch (renderError) {
        // 展示层异常不应中断 SSE 事件消费，否则会丢失澄清/结束事件。
        console.error(renderError)
      }
    }
  }
}

function handleEvent(data: any, currentRecord: ChatRecord) {
  const effect = reduceAgentEvent(currentRecord, data)
  if (effect.waitingUser || effect.terminal === 'failed') {
    runtimeLoading.value = false
    _loading.value = false
  }
  if (effect.terminal === 'failed') emits('error', currentRecord.id)
  if (effect.terminal === 'finished') {
    getChatData(currentRecord.id)
    emits('finish', currentRecord.id)
  }
}

async function sendMessage() {
  stopFlag.value = false
  runtimeLoading.value = true
  _loading.value = true
  if (index.value < 0 || _currentChatId.value === undefined) {
    runtimeLoading.value = false
    _loading.value = false
    return
  }
  const currentRecord: ChatRecord = _currentChat.value.records[index.value]
  if (!currentRecord.question?.trim()) {
    runtimeLoading.value = false
    _loading.value = false
    return
  }
  currentRecord.execution_events = []
  const controller = new AbortController()
  try {
    const response = await agentQuestionApi.stream(
      {
        action: 'start',
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
    runtimeLoading.value = false
    _loading.value = false
  }
}

async function submitClarification(answer: AgentClarificationAnswer) {
  if (index.value < 0) return
  const currentRecord: ChatRecord = _currentChat.value.records[index.value]
  if (!currentRecord.id) return
  stopFlag.value = false
  runtimeLoading.value = true
  _loading.value = true
  currentRecord.status = 'running'
  currentRecord.clarification = undefined
  await pushOptimisticClarificationAccepted(currentRecord)
  const controller = new AbortController()
  try {
    const response = await agentQuestionApi.stream(
      {
        action: 'resume',
        record_id: currentRecord.id,
        clarification: answer,
      },
      controller
    )
    await consumeStream(response, currentRecord, controller)
  } catch (error) {
    currentRecord.error = `Error:${error}`
    emits('error', currentRecord.id)
  } finally {
    runtimeLoading.value = false
    _loading.value = false
  }
}

function cancelClarification() {
  // 跳过澄清：收起卡片并解锁输入框；agent 链路有 cancel API，顺带终止服务端 run。
  if (index.value < 0) return
  const currentRecord: ChatRecord = _currentChat.value.records[index.value]
  currentRecord.clarification = undefined
  const runId = Number(currentRecord.run_id)
  if (runId) {
    agentQuestionApi.cancel(runId).catch(() => {})
  }
  runtimeLoading.value = false
  _loading.value = false
  emits('stop')
}

async function restorePendingClarification() {
  const currentRecord = props.message?.record
  if (!currentRecord?.id || currentRecord.status !== 'waiting_user') return
  const timeline = await agentQuestionApi.timeline(currentRecord.id)
  currentRecord.execution_events = timeline.events
  if (timeline.clarification?.status === 'pending') {
    currentRecord.clarification = timeline.clarification
  }
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
  runtimeLoading.value = false
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

defineExpose({
  sendMessage,
  index: () => index.value,
  stop,
  submitClarification,
  cancelClarification,
})
</script>

<template>
  <BaseAnswer v-if="message" :message="message" :reasoning-name="reasoningName" :loading="_loading">
    <AgentTimeline
      :record="message.record"
      :record-id="message.record?.id"
      :runtime-loading="runtimeLoading"
    />
    <section v-if="finalAnswer" class="final-answer">
      <MdComponent :message="finalAnswer" />
    </section>
    <ChartBlock
      style="margin-top: 6px"
      :message="message"
      :record-id="recordId"
      :loading-data="loadingData"
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

<style scoped lang="less">
.final-answer {
  margin-top: 10px;
  color: rgba(31, 35, 41, 1);
  font-size: 15px;
  line-height: 24px;
}
</style>
