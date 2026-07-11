<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { agentQuestionApi, type AgentTraceResponse } from '@/api/agent-chat'

const props = defineProps<{
  recordId?: number
  refreshKey?: number
}>()

const trace = ref<AgentTraceResponse>()

async function loadTrace() {
  if (!props.recordId) return
  trace.value = await agentQuestionApi.trace(props.recordId)
}

onMounted(loadTrace)
watch(() => props.refreshKey, loadTrace)

function stepBrief(summary?: Record<string, any>) {
  if (!summary) return ''
  if (summary.sql) return String(summary.sql).slice(0, 120)
  if (summary.row_count !== undefined) return `${summary.row_count} 行`
  if (summary.status) return String(summary.status)
  if (summary.count !== undefined) return `${summary.count} 条`
  return ''
}
</script>

<template>
  <el-collapse v-if="trace?.steps?.length" class="agent-trace">
    <el-collapse-item name="trace">
      <template #title>
        执行详情
        <el-tag v-if="trace.budget?.steps" size="small" type="info" class="budget-tag">
          {{ trace.budget.steps }} 轮 · {{ trace.budget.tokens_used || 0 }} tokens
        </el-tag>
      </template>
      <div v-for="step in trace.steps" :key="step.index" class="trace-step">
        <span>
          {{ step.index }}. {{ step.tool_name || 'planning' }}
          {{ step.latency_ms !== undefined && step.latency_ms !== null ? ` · ${step.latency_ms}ms` : '' }}
          <span v-if="stepBrief(step.result_summary)" class="step-brief">
            {{ stepBrief(step.result_summary) }}
          </span>
        </span>
        <el-tag
          size="small"
          :type="step.status === 'failed' ? 'danger' : step.status === 'success' ? 'success' : 'info'"
        >
          {{ step.status }}
        </el-tag>
        <div v-if="step.error" class="step-error">{{ step.error }}</div>
      </div>
    </el-collapse-item>
  </el-collapse>
</template>

<style scoped lang="less">
.agent-trace {
  margin-top: 8px;

  .budget-tag {
    margin-left: 8px;
  }

  .trace-step {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 0;
    font-size: 12px;
    color: var(--el-text-color-secondary);

    .step-brief {
      color: var(--el-text-color-placeholder);
      margin-left: 4px;
    }

    .step-error {
      color: var(--el-color-danger);
    }
  }
}
</style>
