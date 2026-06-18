<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  agenticQuestionApi,
  type AgenticSlotIssue,
  type AgenticTraceResponse,
  type AgenticUnderstandingSummary,
} from '@/api/agentic-chat'

const props = defineProps<{
  recordId?: number
  refreshKey?: number
}>()

const trace = ref<AgenticTraceResponse>()

async function loadTrace() {
  if (!props.recordId) return
  trace.value = await agenticQuestionApi.trace(props.recordId)
}

onMounted(loadTrace)
watch(() => props.refreshKey, loadTrace)

function slotLabels(slots?: Record<string, any>) {
  if (!slots) return []
  return Object.entries(slots).map(([slot, values]) => {
    const list = Array.isArray(values) ? values : [values]
    const names = list
      .map((item) => item?.display_name || item?.name || item?.value || item)
      .filter(Boolean)
      .join('、')
    return `${slot}: ${names}`
  })
}

function issueCandidates(issue: AgenticSlotIssue) {
  return (issue.candidates || [])
    .map((candidate) => candidate.display_name || candidate.raw_text)
    .filter(Boolean)
    .join('、')
}

function hasUnderstandingDetail(understanding?: AgenticUnderstandingSummary) {
  if (!understanding) return false
  return Boolean(
    understanding.intent ||
      slotLabels(understanding.slots).length ||
      understanding.missing_slots?.length ||
      understanding.low_confidence_slots?.length ||
      understanding.ambiguous_slots?.length ||
      understanding.conflict_slots?.length
  )
}
</script>

<template>
  <el-collapse v-if="trace?.steps?.length" class="agentic-trace">
    <el-collapse-item title="执行详情" name="trace">
      <div v-for="step in trace.steps" :key="step.index" class="trace-step">
        <span>
          {{ step.index }}. {{ step.action }}
          {{ step.tool_name ? ` · ${step.tool_name}` : '' }}
          {{ step.strategy ? ` · ${step.strategy}` : '' }}
          {{ step.duration_ms !== undefined && step.duration_ms !== null ? ` · ${step.duration_ms}ms` : '' }}
        </span>
        <el-tag
          size="small"
          :type="step.status === 'failed' ? 'danger' : step.status === 'success' ? 'success' : 'info'"
        >
          {{ step.status }}
        </el-tag>
        <div
          v-if="hasUnderstandingDetail(step.understanding)"
          class="understanding-detail"
        >
          <div class="understanding-line">
            <el-tag v-if="step.understanding?.intent" size="small" type="info">
              {{ step.understanding.intent }}
            </el-tag>
            <span v-if="step.understanding?.normalized_question" class="muted">
              {{ step.understanding.normalized_question }}
            </span>
          </div>
          <div v-if="slotLabels(step.understanding?.slots).length" class="chip-row">
            <el-tag
              v-for="label in slotLabels(step.understanding?.slots)"
              :key="label"
              size="small"
            >
              {{ label }}
            </el-tag>
          </div>
          <div
            v-if="step.understanding?.missing_slots?.length"
            class="issue-row"
          >
            <span class="issue-label">缺失</span>
            <span>{{ step.understanding.missing_slots.join('、') }}</span>
          </div>
          <div
            v-for="issue in step.understanding?.ambiguous_slots || []"
            :key="`ambiguous-${issue.slot}-${issue.raw_text || ''}`"
            class="issue-row"
          >
            <span class="issue-label">歧义</span>
            <span>{{ issue.reason || issue.slot }}</span>
            <span v-if="issueCandidates(issue)" class="muted">{{ issueCandidates(issue) }}</span>
          </div>
          <div
            v-for="issue in step.understanding?.low_confidence_slots || []"
            :key="`low-${issue.slot}-${issue.raw_text || ''}`"
            class="issue-row"
          >
            <span class="issue-label">低置信</span>
            <span>{{ issue.reason || issue.slot }}</span>
          </div>
          <div
            v-for="issue in step.understanding?.conflict_slots || []"
            :key="`conflict-${issue.slot}-${issue.raw_text || ''}`"
            class="issue-row"
          >
            <span class="issue-label">冲突</span>
            <span>{{ issue.reason || issue.slot }}</span>
          </div>
        </div>
      </div>
    </el-collapse-item>
  </el-collapse>
</template>

<style scoped lang="less">
.agentic-trace {
  margin-top: 8px;
}

.trace-step {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 12px;
}

.understanding-detail {
  flex-basis: 100%;
  display: grid;
  gap: 6px;
  margin-top: 6px;
  color: var(--el-text-color-regular);
}

.understanding-line,
.chip-row,
.issue-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  min-width: 0;
}

.issue-label {
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.muted {
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}
</style>
