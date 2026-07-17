<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import { chatApi, ChatInfo, type ChatMessage } from '@/api/chat.ts'
import { computed, onMounted, ref } from 'vue'
import ChartBlock from '@/views/chat/chat-block/ChartBlock.vue'

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

const index = computed(() => props.message?.index ?? -1)
const loadingData = ref(false)

function getChatData(recordId?: number) {
  if (!recordId) return
  loadingData.value = true
  chatApi
    .get_chart_data(recordId)
    .then((response) => {
      props.currentChat.records.forEach((record) => {
        if (record.id === recordId) record.data = response
      })
    })
    .finally(() => {
      loadingData.value = false
      emits('scrollBottom')
    })
}

onMounted(() => {
  if (props.message?.record?.id && props.message.record.finish) {
    getChatData(props.message.record.id)
  }
})

// 该组件只负责展示旧历史记录，不再暴露问数发送入口。
defineExpose({ index: () => index.value })
</script>

<template>
  <BaseAnswer v-if="message" :message="message" :reasoning-name="reasoningName" :loading="false">
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

<style scoped lang="less"></style>
