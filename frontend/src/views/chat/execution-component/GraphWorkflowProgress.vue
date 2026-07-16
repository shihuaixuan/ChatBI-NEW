<script setup lang="ts">
import { computed, ref } from 'vue'
import type {
  GraphEventResponse,
  GraphPendingInteraction,
  GraphTraceResponse,
} from '@/api/graph-workflow'
import {
  buildVisibleGraphWorkflowSteps,
  graphProgressHeadline,
  type GraphWorkflowStep,
} from './graphWorkflowDisplay'

const props = withDefaults(
  defineProps<{
    trace?: GraphTraceResponse
    events?: GraphEventResponse[]
    pendingInteraction?: GraphPendingInteraction | null
    loading?: boolean
  }>(),
  {
    trace: undefined,
    events: () => [],
    pendingInteraction: undefined,
    loading: false,
  }
)

const emits = defineEmits<{
  refresh: []
}>()

const expanded = ref(true)
const displayTrace = computed(() => (props.loading ? undefined : props.trace))
const steps = computed<GraphWorkflowStep[]>(() => {
  return buildVisibleGraphWorkflowSteps(
    displayTrace.value,
    props.pendingInteraction,
    props.events,
    props.loading
  )
})
const headline = computed(() =>
  graphProgressHeadline(displayTrace.value, props.pendingInteraction, steps.value)
)

function statusText(status: string) {
  return (
    {
      pending: '未开始',
      running: '进行中',
      succeeded: '完成',
      failed: '失败',
      waiting_input: '等待补充',
      skipped: '已跳过',
      cancelled: '已停止',
    }[status] || status
  )
}
</script>

<template>
  <section v-if="trace || loading || events.length" class="graph-progress">
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

    <div v-if="expanded" class="step-detail">
      <article v-for="step in steps" :key="step.key" class="step-row" :class="`is-${step.status}`">
        <span class="step-marker">
          <span class="dot"></span>
        </span>
        <div class="step-main">
          <div class="step-title">
            <span>{{ step.label }}</span>
            <span class="step-status">{{ statusText(step.status) }}</span>
          </div>
          <div v-if="step.summary" class="step-summary">{{ step.summary }}</div>
          <pre v-if="step.details?.sql" class="step-sql">{{ step.details.sql }}</pre>
          <div v-if="step.details?.queries?.length" class="split-query-list">
            <section
              v-for="(query, queryIndex) in step.details.queries"
              :key="`${step.key}-${queryIndex}`"
              class="split-query"
            >
              <div class="split-query-title">{{ query.title }}</div>
              <pre v-if="query.sql" class="step-sql">{{ query.sql }}</pre>
              <div v-if="query.rows?.length" class="step-result">
                <table>
                  <thead>
                    <tr>
                      <th v-for="column in query.columns" :key="column">{{ column }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, rowIndex) in query.rows" :key="rowIndex">
                      <td v-for="column in query.columns" :key="column">
                        {{ row[column] ?? '' }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
          <div v-if="step.details?.rows?.length" class="step-result">
            <table>
              <thead>
                <tr>
                  <th v-for="column in step.details.columns" :key="column">{{ column }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, rowIndex) in step.details.rows" :key="rowIndex">
                  <td v-for="column in step.details.columns" :key="column">
                    {{ row[column] ?? '' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
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

.dot {
  margin-top: 6px;
}

.pulse.active {
  background: var(--ed-color-primary);
  box-shadow: 0 0 0 4px rgba(51, 112, 255, 0.16);
}

.actions {
  display: flex;
  flex: 0 0 auto;
  gap: 4px;
}

.step-detail {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.split-query-list {
  display: grid;
  gap: 10px;
  margin-top: 8px;
}

.split-query {
  min-width: 0;
}

.split-query-title {
  margin-bottom: 4px;
  color: rgba(31, 35, 41, 0.72);
  font-size: 13px;
  font-weight: 500;
}

.step-row {
  position: relative;
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr);
  column-gap: 12px;
  align-items: start;
}

.step-marker {
  position: relative;
  display: flex;
  justify-content: center;
  min-height: 100%;
}

.step-marker::after {
  position: absolute;
  top: 20px;
  bottom: -10px;
  left: 50%;
  width: 1px;
  content: '';
  background: rgba(31, 35, 41, 0.12);
  transform: translateX(-50%);
}

.step-row:last-child .step-marker::after {
  display: none;
}

.step-row.is-succeeded .step-marker::after {
  background: rgba(51, 112, 255, 0.22);
}

.step-row.is-succeeded .dot {
  background: var(--ed-color-primary);
}

.step-row.is-running .dot,
.step-row.is-waiting_input .dot {
  background: rgba(51, 112, 255, 1);
}

.step-row.is-running .dot {
  animation: graph-running-dot 1.1s ease-in-out infinite;
}

.step-row.is-running .step-marker::after,
.step-row.is-waiting_input .step-marker::after {
  background: linear-gradient(
    180deg,
    rgba(51, 112, 255, 0.18),
    rgba(51, 112, 255, 0.72),
    rgba(51, 112, 255, 0.18)
  );
  background-size: 100% 24px;
  animation: graph-running-line 1s linear infinite;
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
  line-height: 20px;
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

.step-sql {
  margin: 6px 0 0;
  padding: 8px 10px;
  overflow: auto;
  border: 1px solid rgba(31, 35, 41, 0.1);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.78);
  color: rgba(31, 35, 41, 0.86);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 18px;
  white-space: pre-wrap;
}

.step-result {
  margin-top: 6px;
  max-width: 100%;
  overflow: auto;
  border: 1px solid rgba(31, 35, 41, 0.1);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.78);
}

.step-result table {
  width: 100%;
  min-width: 240px;
  border-collapse: collapse;
  font-size: 12px;
  line-height: 18px;
}

.step-result th,
.step-result td {
  padding: 6px 8px;
  border-bottom: 1px solid rgba(31, 35, 41, 0.08);
  color: rgba(31, 35, 41, 0.78);
  text-align: left;
  white-space: nowrap;
}

.step-result th {
  background: rgba(31, 35, 41, 0.04);
  color: rgba(31, 35, 41, 0.64);
  font-weight: 500;
}

.step-result tr:last-child td {
  border-bottom: 0;
}

@keyframes graph-running-dot {
  0% {
    box-shadow: 0 0 0 0 rgba(51, 112, 255, 0.28);
    transform: scale(1);
  }
  50% {
    box-shadow: 0 0 0 5px rgba(51, 112, 255, 0.12);
    transform: scale(1.18);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(51, 112, 255, 0);
    transform: scale(1);
  }
}

@keyframes graph-running-line {
  from {
    background-position: 0 0;
  }
  to {
    background-position: 0 24px;
  }
}
</style>
