<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { AgentClarification, AgentClarificationAnswer } from '@/api/agent-chat'

const props = defineProps<{
  clarification?: AgentClarification
  disabled?: boolean
}>()

const emits = defineEmits<{
  submit: [answer: AgentClarificationAnswer]
  cancel: []
}>()

const state = reactive<{ selected: string; text: string }>({ selected: '', text: '' })
const submitted = ref(false)

const hasOptions = computed(() => (props.clarification?.options || []).length > 0)

const canSubmit = computed(() => Boolean(state.selected || state.text.trim()))

function toggleOption(value?: string) {
  if (!value || props.disabled || submitted.value) return
  state.selected = state.selected === value ? '' : value
}

function submit() {
  if (submitted.value || props.disabled || !canSubmit.value) return
  const option = (props.clarification?.options || []).find((item) => item.value === state.selected)
  const answer: AgentClarificationAnswer = {
    selections: option ? [{ label: option.label, value: option.value }] : [],
    text: state.text.trim() || undefined,
  }
  submitted.value = true
  emits('submit', answer)
}

function cancel() {
  emits('cancel')
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
    <div class="clarify-head">
      <span class="clarify-badge">需要补充</span>
      <span class="clarify-question">{{ clarification.question }}</span>
    </div>
    <div class="clarify-body">
      <div v-if="hasOptions" class="chip-row">
        <button
          v-for="option in clarification.options"
          :key="option.value"
          type="button"
          class="chip"
          :class="{ active: state.selected === option.value }"
          :disabled="disabled || submitted"
          @click="toggleOption(option.value)"
        >
          {{ option.label || option.value }}
        </button>
      </div>
      <el-input
        v-model="state.text"
        :disabled="disabled || submitted"
        :placeholder="hasOptions ? '或输入补充说明' : '请补充关键信息'"
        @keydown.enter.prevent="submit"
      />
    </div>
    <div class="clarify-actions">
      <el-button
        type="primary"
        :disabled="disabled || submitted || !canSubmit"
        :loading="submitted"
        @click="submit"
      >
        提交
      </el-button>
      <el-button text :disabled="disabled || submitted" @click="cancel">跳过</el-button>
    </div>
  </div>
</template>

<style scoped lang="less">
.clarification-card {
  width: 100%;
  padding: 16px;
  border: 1px solid var(--ed-color-primary, rgba(28, 186, 144, 1));
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 4px 12px rgba(31, 35, 41, 0.06);
}

.clarify-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;

  .clarify-badge {
    flex-shrink: 0;
    padding: 1px 8px;
    border-radius: 4px;
    font-size: 12px;
    line-height: 20px;
    color: var(--ed-color-primary, rgba(28, 186, 144, 1));
    background: var(--ed-color-primary-1a, rgba(28, 186, 144, 0.1));
  }

  .clarify-question {
    font-size: 14px;
    font-weight: 500;
    line-height: 22px;
    color: rgba(31, 35, 41, 1);
    overflow-wrap: anywhere;
  }
}

.clarify-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;

  .chip {
    padding: 5px 14px;
    border: 1px solid rgba(217, 220, 223, 1);
    border-radius: 15px;
    background: #fff;
    font-size: 13px;
    line-height: 18px;
    color: rgba(31, 35, 41, 1);
    cursor: pointer;
    transition:
      border-color 0.15s,
      background 0.15s;

    &:hover:not(:disabled) {
      border-color: var(--ed-color-primary, rgba(28, 186, 144, 1));
    }

    &.active {
      border-color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      background: var(--ed-color-primary-1a, rgba(28, 186, 144, 0.1));
    }

    &:disabled {
      cursor: not-allowed;
      opacity: 0.6;
    }
  }
}

.clarify-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 12px;
}
</style>
