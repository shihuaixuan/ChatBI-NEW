<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { AgentClarification, AgentClarificationAnswer } from '@/api/agent-chat'

const props = defineProps<{
  clarification?: AgentClarification
  disabled?: boolean
}>()

const emits = defineEmits<{
  submit: [answer: AgentClarificationAnswer]
}>()

const state = reactive<{ selected: string; text: string }>({ selected: '', text: '' })
const submitted = ref(false)

const hasOptions = computed(() => (props.clarification?.options || []).length > 0)

const canSubmit = computed(() => Boolean(state.selected || state.text.trim()))

function submit() {
  if (submitted.value || !canSubmit.value) return
  const option = (props.clarification?.options || []).find((item) => item.value === state.selected)
  const answer: AgentClarificationAnswer = {
    selections: option ? [{ label: option.label, value: option.value }] : [],
    text: state.text.trim() || undefined,
  }
  submitted.value = true
  emits('submit', answer)
}

watch(
  () => props.clarification?.clarification_id || props.clarification?.question,
  () => {
    state.selected = ''
    state.text = ''
    submitted.value = false
  },
  { immediate: true }
)
</script>

<template>
  <div v-if="clarification" class="clarification-card">
    <div class="clarification-title">{{ clarification.question }}</div>
    <div class="clarification-form">
      <el-radio-group
        v-if="hasOptions"
        v-model="state.selected"
        :disabled="disabled || submitted"
        class="option-group"
      >
        <el-radio
          v-for="option in clarification.options"
          :key="option.value"
          :value="option.value"
          class="option-item"
        >
          {{ option.label || option.value }}
        </el-radio>
      </el-radio-group>
      <el-input
        v-model="state.text"
        :disabled="disabled || submitted"
        :placeholder="hasOptions ? '或输入补充说明' : '请补充关键信息'"
        size="small"
      />
      <div class="clarification-actions">
        <el-button
          type="primary"
          size="small"
          :disabled="disabled || submitted || !canSubmit"
          @click="submit"
        >
          提交
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped lang="less">
.clarification-card {
  margin-top: 8px;
  padding: 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);

  .clarification-title {
    font-weight: 600;
    margin-bottom: 8px;
  }

  .option-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 8px;

    .option-item {
      margin-right: 0;
    }
  }

  .clarification-actions {
    margin-top: 8px;
  }
}
</style>
