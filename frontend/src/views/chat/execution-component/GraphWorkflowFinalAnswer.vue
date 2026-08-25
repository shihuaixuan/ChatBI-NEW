<script setup lang="ts">
import { computed } from 'vue'
import type { ChatRecord } from '@/api/chat'
import MdComponent from '@/views/chat/component/MdComponent.vue'
import { projectResearchReportAnswer } from '@/views/chat/answer/researchReportProjection'

const props = withDefaults(
  defineProps<{
    record?: ChatRecord
    loading?: boolean
    waitingInput?: boolean
  }>(),
  {
    record: undefined,
    loading: false,
    waitingInput: false,
  }
)

const answer = computed(() =>
  projectResearchReportAnswer(props.record?.chart_answer || props.record?.sql_answer || '')
)
</script>

<template>
  <section v-if="answer || (loading && !waitingInput)" class="final-answer">
    <MdComponent v-if="answer" :message="answer" />
    <span v-else class="placeholder">正在整理答案...</span>
  </section>
</template>

<style scoped lang="less">
.final-answer {
  margin-top: 10px;
  color: rgba(31, 35, 41, 1);
  font-size: 15px;
  line-height: 24px;
}

.placeholder {
  color: rgba(143, 149, 158, 1);
}
</style>
