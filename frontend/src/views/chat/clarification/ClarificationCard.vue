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

function answerForSlot(slot: string): AgenticClarificationAnswerItem | undefined {
  const value = form[slot]
  if (value === '__other__') {
    const otherValue = (otherForm[slot] || '').trim()
    return otherValue ? { slot, value: otherValue } : undefined
  }
  const answerValue = Array.isArray(value) ? value.join(',') : (value || '').trim()
  return answerValue ? { slot, value: answerValue } : undefined
}

const canSubmit = computed(() => currentSlots.value.some((slot) => answerForSlot(slot)))

function submit() {
  if (submitted.value) return
  const answers = currentSlots.value
    .map((slot) => answerForSlot(slot))
    .filter((item): item is AgenticClarificationAnswerItem => Boolean(item))
  if (answers.length > 0) {
    submitted.value = true
    emits('submit', answers)
  }
}

function cancel() {
  submitted.value = false
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
    <div class="clarification-title">{{ clarification.question }}</div>
    <div class="clarification-form">
      <template v-for="slot in clarification.target_slots" :key="slot">
        <el-checkbox-group
          v-if="optionSlots.has(slot) && isMultiple(slot)"
          v-model="form[slot]"
          :disabled="disabled || submitted"
        >
          <el-checkbox
            v-for="option in optionsForSlot(slot)"
            :key="option.value"
            :label="option.value"
          >
            {{ option.label || option.value }}
          </el-checkbox>
        </el-checkbox-group>
        <template v-else-if="optionSlots.has(slot)">
          <el-radio-group
            v-model="form[slot]"
            :disabled="disabled || submitted"
            class="option-group"
          >
            <el-radio
              v-for="option in optionsForSlot(slot)"
              :key="option.value"
              :value="option.value"
              class="option-item"
            >
              {{ option.label || option.value }}
            </el-radio>
          </el-radio-group>
          <el-input
            v-if="form[slot] === '__other__'"
            v-model="otherForm[slot]"
            :disabled="disabled || submitted"
            placeholder="请补充"
            size="small"
          />
        </template>
        <el-input
          v-else
          v-model="form[slot]"
          :disabled="disabled || submitted"
          :placeholder="slotPlaceholder(slot)"
          size="small"
        />
      </template>
      <div class="clarification-actions">
        <el-button
          type="primary"
          size="small"
          :disabled="disabled || submitted || !canSubmit"
          @click="submit"
        >
          提交
        </el-button>
        <el-button size="small" :disabled="disabled || submitted" @click="cancel">取消</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped lang="less">
.clarification-card {
  margin-top: 8px;
  padding: 10px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}

.clarification-title {
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--el-text-color-primary);
}

.clarification-form {
  display: grid;
  grid-template-columns: minmax(160px, 1fr);
  gap: 8px;
}

.option-group {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
}

.option-item {
  max-width: 100%;
  margin-right: 0;
  white-space: normal;
  overflow-wrap: anywhere;
}

.clarification-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
</style>
