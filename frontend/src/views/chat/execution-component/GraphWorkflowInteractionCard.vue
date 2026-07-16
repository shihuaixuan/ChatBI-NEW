<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { GraphPendingInteraction } from '@/api/graph-workflow'
import {
  buildGraphInteractionResponse,
  graphInteractionOptionKey,
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

const selectedKey = ref('')
const customValue = ref('')
const dimensionValues = ref<Record<string, string>>({})
const submitted = ref(false)
const normalized = computed(() => normalizeGraphInteraction(props.interaction))
const hasOptions = computed(() => normalized.value.options.length > 0)
const selectedOption = computed(() =>
  normalized.value.options.find((option, index) => optionKey(option, index) === selectedKey.value)
)
const dimensionValueFields = computed(() => {
  const optionFields = selectedOption.value?.value?.dimension_value_fields
  if (Array.isArray(optionFields) && optionFields.length) return optionFields.map(String)
  if (selectedOption.value?.value?.dimension_usage === 'filter_value_required') {
    const dimension = selectedOption.value.value.dimension
    if (dimension) return [String(dimension)]
  }
  return normalized.value.dimensionValueFields
})
const needsDimensionValues = computed(
  () =>
    selectedOption.value?.value?.dimension_usage === 'filter_value_required' &&
    dimensionValueFields.value.length > 0
)
const canSubmit = computed(() => {
  if (needsDimensionValues.value) {
    return dimensionValueFields.value.every((field) => dimensionValues.value[field]?.trim())
  }
  return Boolean(selectedOption.value || customValue.value.trim())
})

function optionKey(option: NormalizedGraphInteractionOption, index: number) {
  return graphInteractionOptionKey(option, index)
}

function choose(option: NormalizedGraphInteractionOption, index: number) {
  selectedKey.value = optionKey(option, index)
  customValue.value = ''
  dimensionValues.value = {}
}

function submit() {
  if (!props.interaction || submitted.value || !canSubmit.value) return
  submitted.value = true
  emits(
    'submit',
    buildGraphInteractionResponse(
      props.interaction,
      selectedOption.value,
      needsDimensionValues.value ? dimensionValues.value : customValue.value
    )
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
    selectedKey.value = ''
    customValue.value = ''
    dimensionValues.value = {}
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
        v-for="(option, optionIndex) in normalized.options"
        :key="optionKey(option, optionIndex)"
        type="button"
        class="option-btn"
        :class="{ selected: selectedKey === optionKey(option, optionIndex) }"
        :aria-pressed="selectedKey === optionKey(option, optionIndex)"
        :disabled="disabled || submitted"
        @pointerdown="choose(option, optionIndex)"
        @click="choose(option, optionIndex)"
      >
        {{ option.label }}
      </button>
    </div>

    <div v-if="needsDimensionValues" class="dimension-value-list">
      <label
        v-for="field in dimensionValueFields"
        :key="field"
        class="dimension-value-row"
      >
        <span class="dimension-value-label">{{ field }}</span>
        <input
          v-model="dimensionValues[field]"
          type="text"
          :disabled="disabled || submitted"
          class="custom-input"
          placeholder="请输入具体值"
        >
      </label>
    </div>

    <input
      v-else
      v-model="customValue"
      type="text"
      :disabled="disabled || submitted"
      class="custom-input"
      placeholder="其他，请补充"
      @input="selectedKey = ''"
    >

    <div class="card-actions">
      <button
        type="button"
        class="action-btn primary"
        :disabled="disabled || submitted || !canSubmit"
        @click="submit"
      >
        提交并继续
      </button>
      <button
        type="button"
        class="action-btn"
        :disabled="disabled || submitted"
        @click="skip"
      >
        无法补充，结束本次问答
      </button>
      <span v-if="submitted" class="submitted-text">已提交，继续执行中...</span>
    </div>
  </section>
</template>

<style scoped lang="less">
.interaction-card {
  margin-top: 10px;
  padding: 12px;
  border: 1px solid rgba(51, 112, 255, 0.35);
  border-radius: 8px;
  background: rgba(51, 112, 255, 0.08);
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

.option-btn.selected {
  background: rgba(51, 112, 255, 0.12);
  font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(51, 112, 255, 0.18);
}

.option-btn:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.option-btn.selected:disabled {
  opacity: 1;
}

.custom-input {
  width: 100%;
  height: 24px;
  box-sizing: border-box;
  margin-top: 10px;
  padding: 1px 8px;
  border: 1px solid rgba(217, 220, 223, 1);
  border-radius: 4px;
  background: #fff;
  color: rgba(31, 35, 41, 1);
  font-size: 13px;
  line-height: 20px;
  outline: none;
}

.custom-input:focus {
  border-color: var(--ed-color-primary);
}

.custom-input:disabled {
  background: rgba(245, 247, 250, 1);
  cursor: not-allowed;
  opacity: 0.65;
}

.custom-input::placeholder {
  color: rgba(143, 149, 158, 1);
}

.dimension-value-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.dimension-value-row {
  display: grid;
  grid-template-columns: minmax(56px, max-content) minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.dimension-value-label {
  color: rgba(31, 35, 41, 1);
  font-size: 13px;
  line-height: 20px;
}

.dimension-value-row .custom-input {
  margin-top: 0;
}

.card-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.action-btn {
  min-height: 24px;
  max-width: 100%;
  padding: 2px 11px;
  border: 1px solid rgba(217, 220, 223, 1);
  border-radius: 4px;
  background: #fff;
  color: rgba(31, 35, 41, 1);
  cursor: pointer;
  font-size: 12px;
  line-height: 18px;
  overflow-wrap: anywhere;
}

.action-btn.primary {
  border-color: var(--ed-color-primary);
  background: var(--ed-color-primary);
  color: #fff;
}

.action-btn:not(:disabled):hover {
  border-color: var(--ed-color-primary);
  color: var(--ed-color-primary);
}

.action-btn.primary:not(:disabled):hover {
  background: var(--ed-color-primary);
  color: #fff;
  opacity: 0.9;
}

.action-btn:disabled {
  border-color: rgba(217, 220, 223, 1);
  background: rgba(245, 247, 250, 1);
  color: rgba(143, 149, 158, 1);
  cursor: not-allowed;
  opacity: 1;
}

.action-btn.primary:disabled {
  border-color: rgba(217, 220, 223, 1);
  background: rgba(245, 247, 250, 1);
  color: rgba(143, 149, 158, 1);
}

.submitted-text {
  color: rgba(100, 106, 115, 1);
  font-size: 12px;
}
</style>
