<script setup lang="ts">
import { computed, ref } from 'vue'
import type { GraphPendingInteraction, GraphTraceResponse } from '@/api/graph-workflow'
import {
  buildGraphWorkflowSteps,
  graphProgressHeadline,
  type GraphWorkflowStep,
} from './graphWorkflowDisplay'

const props = withDefaults(
  defineProps<{
    trace?: GraphTraceResponse
    pendingInteraction?: GraphPendingInteraction | null
    loading?: boolean
  }>(),
  {
    trace: undefined,
    pendingInteraction: undefined,
    loading: false,
  }
)

const emits = defineEmits<{
  refresh: []
}>()

const expanded = ref(false)
const steps = computed<GraphWorkflowStep[]>(() =>
  buildGraphWorkflowSteps(props.trace, props.pendingInteraction)
)
const headline = computed(() => graphProgressHeadline(props.trace, props.pendingInteraction))

function statusText(status: string) {
  return {
    pending: '未开始',
    running: '进行中',
    succeeded: '完成',
    failed: '失败',
    waiting_input: '等待补充',
    skipped: '已跳过',
    cancelled: '已停止',
  }[status] || status
}
</script>

<template>
  <section v-if="trace || loading" class="graph-progress">
    <div class="progress-head">
      <div class="headline">
        <span class="pulse" :class="{ active: loading || trace?.status === 'running' }"></span>
        <span>{{ headline }}</span>
      </div>
      <div class="actions">
        <el-button size="small" text @click="expanded = !expanded">
          {{ expanded ? '收起步骤' : '查看步骤' }}
        </el-button>
        <el-button size="small" text :loading="loading" @click="emits('refresh')">刷新</el-button>
      </div>
    </div>

    <div v-if="steps.length" class="step-strip">
      <span
        v-for="step in steps"
        :key="step.key"
        class="step-chip"
        :class="`is-${step.status}`"
      >
        {{ step.label }}
      </span>
    </div>

    <div v-if="expanded" class="step-detail">
      <article
        v-for="step in steps"
        :key="step.key"
        class="step-row"
        :class="`is-${step.status}`"
      >
        <span class="dot"></span>
        <div class="step-main">
          <div class="step-title">
            <span>{{ step.label }}</span>
            <span class="step-status">{{ statusText(step.status) }}</span>
          </div>
          <div v-if="step.summary" class="step-summary">{{ step.summary }}</div>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped lang="less">
.graph-progress {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid rgba(31, 35, 41, 0.12);
  border-radius: 8px;
  background: rgba(248, 249, 250, 1);
}

.progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.headline {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: rgba(31, 35, 41, 1);
  font-size: 14px;
  font-weight: 500;
}

.pulse,
.dot {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  border-radius: 50%;
  background: rgba(143, 149, 158, 1);
}

.pulse.active {
  background: var(--ed-color-primary);
  box-shadow: 0 0 0 4px rgba(28, 186, 144, 0.16);
}

.actions {
  display: flex;
  flex: 0 0 auto;
  gap: 4px;
}

.step-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.step-chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(31, 35, 41, 0.06);
  color: rgba(100, 106, 115, 1);
  font-size: 12px;
}

.step-chip.is-succeeded {
  background: rgba(28, 186, 144, 0.12);
  color: rgba(24, 158, 122, 1);
}

.step-chip.is-running,
.step-chip.is-waiting_input {
  background: rgba(64, 128, 255, 0.12);
  color: rgba(51, 112, 255, 1);
}

.step-chip.is-failed {
  background: rgba(245, 74, 69, 0.12);
  color: rgba(216, 57, 49, 1);
}

.step-detail {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.step-row {
  display: flex;
  gap: 8px;
}

.step-row.is-succeeded .dot {
  background: var(--ed-color-primary);
}

.step-row.is-running .dot,
.step-row.is-waiting_input .dot {
  background: rgba(51, 112, 255, 1);
}

.step-row.is-failed .dot {
  background: rgba(216, 57, 49, 1);
}

.step-main {
  min-width: 0;
  flex: 1;
}

.step-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgba(31, 35, 41, 1);
  font-size: 13px;
  font-weight: 500;
}

.step-status {
  margin-left: auto;
  color: rgba(143, 149, 158, 1);
  font-size: 12px;
  font-weight: 400;
}

.step-summary {
  margin-top: 2px;
  color: rgba(100, 106, 115, 1);
  font-size: 12px;
  line-height: 20px;
  overflow-wrap: anywhere;
}
</style>
