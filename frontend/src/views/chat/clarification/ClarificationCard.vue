<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { AgenticClarification, AgenticClarificationAnswerItem } from '@/api/agentic-chat'

const props = defineProps<{
  clarification?: AgenticClarification
  disabled?: boolean
}>()

const emits = defineEmits<{
  submit: [answers: AgenticClarificationAnswerItem[]]
  cancel: []
}>()

const form = reactive<Record<string, string | string[]>>({})
const otherForm = reactive<Record<string, string>>({})
const submitted = ref(false)

const currentSlots = computed(() => props.clarification?.target_slots || [])

const optionSlots = computed(() => {
  const slots = new Set<string>()
  for (const option of props.clarification?.options || []) {
    if (option.slot) slots.add(option.slot)
  }
  return slots
})

function optionsForSlot(slot: string) {
  return (props.clarification?.options || []).filter((option) => option.slot === slot)
}

function isMultiple(slot: string) {
  return optionsForSlot(slot).some((option) => option.multiple)
}

const SLOT_LABELS: Record<string, string> = {
  metrics: '指标',
  dimensions: '维度',
  filters: '筛选条件',
  time_range: '时间范围',
  datasource: '数据源',
}

function slotLabel(slot: string) {
  return SLOT_LABELS[slot] || slot
}

function slotPlaceholder(slot: string) {
  const placeholders: Record<string, string> = {
    metrics: '请输入指标名称，如 GMV、销售额',
    dimensions: '请输入维度名称，如 地区、渠道',
    filters: '请输入筛选条件',
    time_range: '请输入时间范围',
    datasource: '请选择数据源',
  }
  return placeholders[slot] || '请补充关键信息'
}

function isSelected(slot: string, value?: string) {
  if (!value) return false
  const current = form[slot]
  return Array.isArray(current) ? current.includes(value) : current === value
}

function toggleOption(slot: string, value?: string) {
  if (!value || props.disabled || submitted.value) return
  if (isMultiple(slot)) {
    const current = Array.isArray(form[slot]) ? [...(form[slot] as string[])] : []
    const index = current.indexOf(value)
    if (index >= 0) current.splice(index, 1)
    else current.push(value)
    form[slot] = current
  } else {
    form[slot] = form[slot] === value ? '' : value
  }
}

function needsOtherInput(slot: string) {
  const current = form[slot]
  return Array.isArray(current) ? current.includes('__other__') : current === '__other__'
}

function answerForSlot(slot: string): AgenticClarificationAnswerItem | undefined {
  const value = form[slot]
  const other = (otherForm[slot] || '').trim()
  if (Array.isArray(value)) {
    const values = value.filter((item) => item !== '__other__')
    if (value.includes('__other__') && other) values.push(other)
    return values.length ? { slot, value: values.join(',') } : undefined
  }
  if (value === '__other__') {
    return other ? { slot, value: other } : undefined
  }
  const answerValue = (value || '').trim()
  return answerValue ? { slot, value: answerValue } : undefined
}

const canSubmit = computed(() => currentSlots.value.some((slot) => answerForSlot(slot)))

function submit() {
  if (submitted.value || props.disabled) return
  const answers = currentSlots.value
    .map((slot) => answerForSlot(slot))
    .filter((item): item is AgenticClarificationAnswerItem => Boolean(item))
  if (answers.length > 0) {
    submitted.value = true
    emits('submit', answers)
  }
}

function cancel() {
  emits('cancel')
}

watch(
  () => props.clarification?.clarification_id || props.clarification?.question,
  () => {
    for (const key of Object.keys(form)) delete form[key]
    for (const key of Object.keys(otherForm)) delete otherForm[key]
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
      <div v-for="slot in currentSlots" :key="slot" class="slot-row">
        <span v-if="currentSlots.length > 1 || !optionSlots.has(slot)" class="slot-label">
          {{ slotLabel(slot) }}
        </span>
        <template v-if="optionSlots.has(slot)">
          <div class="chip-row">
            <button
              v-for="option in optionsForSlot(slot)"
              :key="option.value"
              type="button"
              class="chip"
              :class="{ active: isSelected(slot, option.value) }"
              :disabled="disabled || submitted"
              @click="toggleOption(slot, option.value)"
            >
              {{ option.label || option.value }}
            </button>
          </div>
          <el-input
            v-if="needsOtherInput(slot)"
            v-model="otherForm[slot]"
            :disabled="disabled || submitted"
            placeholder="请输入其他内容"
            @keydown.enter.prevent="submit"
          />
        </template>
        <el-input
          v-else
          v-model="form[slot]"
          :disabled="disabled || submitted"
          :placeholder="slotPlaceholder(slot)"
          @keydown.enter.prevent="submit"
        />
      </div>
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

  .slot-row {
    display: flex;
    flex-direction: column;
    gap: 6px;

    .slot-label {
      font-size: 12px;
      color: rgba(100, 106, 115, 1);
    }
  }
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
