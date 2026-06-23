<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { GraphPendingInteraction } from '@/api/graph-workflow'
import {
  buildGraphInteractionResponse,
  normalizeGraphInteraction,
  type NormalizedGraphInteractionOption,
} from './graphWorkflowDisplay'

const props = withDefaults(
  defineProps<{
    interaction?: GraphPendingInteraction
    disabled?: boolean
  }>(),
  {
    interaction: undefined,
    disabled: false,
  }
)

const emits = defineEmits<{
  submit: [response: Record<string, any>]
  skip: []
}>()

const selected = ref<NormalizedGraphInteractionOption>()
const customValue = ref('')
const submitted = ref(false)
const normalized = computed(() => normalizeGraphInteraction(props.interaction))
const hasOptions = computed(() => normalized.value.options.length > 0)
const canSubmit = computed(() => Boolean(selected.value || customValue.value.trim()))

function choose(option: NormalizedGraphInteractionOption) {
  selected.value = option
  customValue.value = ''
}

function submit() {
  if (!props.interaction || submitted.value || !canSubmit.value) return
  submitted.value = true
  emits(
    'submit',
    buildGraphInteractionResponse(props.interaction, selected.value, customValue.value)
  )
}

function skip() {
  if (submitted.value) return
  submitted.value = true
  emits('skip')
}

watch(
  () => props.interaction?.interaction_id,
  () => {
    selected.value = undefined
    customValue.value = ''
    submitted.value = false
  },
  { immediate: true }
)
</script>

<template>
  <section v-if="interaction?.status === 'pending'" class="interaction-card">
    <div class="card-title">{{ normalized.title || '需要你补充信息' }}</div>
    <div class="card-prompt">{{ normalized.prompt }}</div>

    <div v-if="hasOptions" class="option-list">
      <button
        v-for="option in normalized.options"
        :key="option.label + String(option.value)"
        type="button"
        class="option-btn"
        :class="{ selected: selected?.label === option.label }"
        :disabled="disabled || submitted"
        @click="choose(option)"
      >
        {{ option.label }}
      </button>
    </div>

    <el-input
      v-model="customValue"
      :disabled="disabled || submitted"
      size="small"
      class="custom-input"
      placeholder="其他，请补充"
      @input="selected = undefined"
    />

    <div class="card-actions">
      <el-button
        type="primary"
        size="small"
        :disabled="disabled || submitted || !canSubmit"
        @click="submit"
      >
        提交并继续
      </el-button>
      <el-button size="small" :disabled="disabled || submitted" @click="skip">
        无法补充，结束本次问答
      </el-button>
      <span v-if="submitted" class="submitted-text">已提交，继续执行中...</span>
    </div>
  </section>
</template>

<style scoped lang="less">
.interaction-card {
  margin-top: 10px;
  padding: 12px;
  border: 1px solid rgba(28, 186, 144, 0.35);
  border-radius: 8px;
  background: rgba(28, 186, 144, 0.08);
}

.card-title {
  color: rgba(31, 35, 41, 1);
  font-size: 14px;
  font-weight: 600;
}

.card-prompt {
  margin-top: 6px;
  color: rgba(100, 106, 115, 1);
  font-size: 13px;
  line-height: 22px;
}

.option-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.option-btn {
  max-width: 100%;
  padding: 6px 10px;
  border: 1px solid rgba(217, 220, 223, 1);
  border-radius: 6px;
  background: #fff;
  color: rgba(31, 35, 41, 1);
  cursor: pointer;
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

.option-btn:hover,
.option-btn.selected {
  border-color: var(--ed-color-primary);
  color: var(--ed-color-primary);
}

.option-btn:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.custom-input {
  margin-top: 10px;
}

.card-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.submitted-text {
  color: rgba(100, 106, 115, 1);
  font-size: 12px;
}
</style>
