<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { ChatRecord } from '@/api/chat.ts'
import { agenticQuestionApi } from '@/api/agentic-chat'
import icon_up_outlined from '@/assets/svg/icon_up_outlined.svg'
import icon_down_outlined from '@/assets/svg/icon_down_outlined.svg'

const props = defineProps<{
  record?: ChatRecord
}>()

interface LiveStep {
  key: string
  action: string
  title: string
  status: 'running' | 'success' | 'failed' | 'waiting'
  toolName?: string
  durationMs?: number
  startTs?: number
  sql?: string
  rowCount?: number
  fields?: string[]
  strategy?: string
  reason?: string
  understanding?: Record<string, any>
  question?: string
  errorMessage?: string
}

const ACTION_TITLES: Record<string, string> = {
  understand_query: '理解问题',
  retrieve_evidence: '检索上下文',
  route_strategy: '选择生成策略',
  generate_sql: '生成 SQL',
  validate_sql: '校验 SQL',
  apply_permission: '应用数据权限',
  execute_sql: '执行查询',
  generate_answer: '生成回答',
  ask_clarification: '等待补充信息',
  understanding_clarification: '澄清业务口径',
}

function actionTitle(action?: string) {
  return (action && ACTION_TITLES[action]) || action || '执行步骤'
}

function findLastStep(steps: LiveStep[], predicate: (step: LiveStep) => boolean) {
  for (let i = steps.length - 1; i >= 0; i--) {
    if (predicate(steps[i])) return steps[i]
  }
  return undefined
}

const events = computed<Array<Record<string, any>>>(
  () => (props.record?.agentic_trace as Array<Record<string, any>>) || []
)

const steps = computed<LiveStep[]>(() => {
  const result: LiveStep[] = []
  const byIndex = new Map<number, LiveStep>()
  let running: LiveStep | undefined
  for (const event of events.value) {
    switch (event.type) {
      case 'step-started': {
        const step: LiveStep = {
          key: `step-${event.step_index ?? result.length}`,
          action: event.action,
          title: actionTitle(event.action),
          status: 'running',
          startTs: event._ts,
        }
        result.push(step)
        if (event.step_index !== undefined) byIndex.set(event.step_index, step)
        running = step
        break
      }
      case 'tool-called':
        if (running) running.toolName = event.tool_name
        break
      case 'workflow-step':
        if (running) {
          running.action = event.action
          running.title = actionTitle(event.action)
        }
        break
      case 'step-finished': {
        const step = (event.step_index !== undefined && byIndex.get(event.step_index)) || running
        if (step) {
          step.status = 'success'
          const summary = event.summary || {}
          if (summary.sql) step.sql = summary.sql
          if (summary.row_count !== undefined) step.rowCount = summary.row_count
          if (summary.fields?.length) step.fields = summary.fields
          if (event._ts && step.startTs) step.durationMs = event._ts - step.startTs
          if (running === step) running = undefined
        }
        break
      }
      case 'understanding': {
        const step = findLastStep(result, (item) => item.action === 'understand_query') || running
        if (step) step.understanding = event
        break
      }
      case 'route-selected': {
        const step = findLastStep(result, (item) => item.action === 'route_strategy')
        if (step) {
          step.strategy = event.strategy
          step.reason = event.reason
        }
        break
      }
      case 'sql-generated': {
        const step = findLastStep(result, (item) => item.action === 'generate_sql')
        if (step) step.sql = event.sql
        break
      }
      case 'sql-validated': {
        const step = findLastStep(result, (item) => item.action === 'validate_sql')
        if (step) step.sql = event.sql
        break
      }
      case 'sql-executed': {
        const step = findLastStep(result, (item) => item.action === 'execute_sql')
        if (step) {
          step.rowCount = event.row_count
          step.fields = event.fields
        }
        break
      }
      case 'clarification': {
        result.push({
          key: `clarify-${event.clarification_id ?? result.length}`,
          action: 'ask_clarification',
          title: '等待补充信息',
          status: 'waiting',
          question: event.question,
        })
        running = undefined
        break
      }
      case 'clarification-accepted': {
        const step = findLastStep(result, (item) => item.action === 'ask_clarification')
        if (step && step.status === 'waiting') {
          step.status = 'success'
          step.title = '已补充信息'
        }
        break
      }
      case 'run-failed':
      case 'error': {
        const target = running || findLastStep(result, (item) => item.status === 'running')
        if (target) {
          target.status = 'failed'
          target.errorMessage = event.content
        } else {
          result.push({
            key: `failed-${result.length}`,
            action: 'failed',
            title: '执行失败',
            status: 'failed',
            errorMessage: event.content,
          })
        }
        running = undefined
        break
      }
      case 'run-finished':
      case 'finish': {
        for (const step of result) {
          if (step.status === 'running') step.status = 'success'
        }
        running = undefined
        break
      }
    }
  }
  return result
})

const runningStep = computed(() => findLastStep(steps.value, (step) => step.status === 'running'))
const waitingStep = computed(() => findLastStep(steps.value, (step) => step.status === 'waiting'))
const failedStep = computed(() => findLastStep(steps.value, (step) => step.status === 'failed'))

const headerStatus = computed<'running' | 'waiting' | 'failed' | 'done'>(() => {
  if (runningStep.value) return 'running'
  if (waitingStep.value) return 'waiting'
  if (failedStep.value) return 'failed'
  return 'done'
})

const headerText = computed(() => {
  if (runningStep.value) return `${runningStep.value.title}…`
  if (waitingStep.value) return '等待补充信息'
  if (failedStep.value) return '执行失败'
  return `已完成 ${steps.value.length} 个步骤`
})

const expanded = ref(true)

watch(
  () => headerStatus.value,
  (status, previous) => {
    // 运行结束自动折叠，重新运行（追问续跑）时再展开。
    if (status === 'done' && previous && previous !== 'done') expanded.value = false
    if (status === 'running') expanded.value = true
  }
)

// SSE 事件经 JSONBig.parse 产生无原型对象，不能依赖隐式 toString，统一走安全格式化。
function formatSlotValue(item: any): string {
  if (item === null || item === undefined) return ''
  if (typeof item !== 'object') return String(item)
  const label = item.display_name ?? item.name ?? item.value ?? item.raw_text
  if (label !== null && label !== undefined && typeof label !== 'object') return String(label)
  if (item.start || item.end) return [item.start, item.end].filter(Boolean).join(' ~ ')
  try {
    return JSON.stringify(item)
  } catch {
    return ''
  }
}

function slotLabels(slots?: Record<string, any>) {
  if (!slots) return []
  return Object.entries(slots).map(([slot, values]) => {
    const list = Array.isArray(values) ? values : [values]
    const names = list.map(formatSlotValue).filter(Boolean).join('、')
    return `${slot}: ${names}`
  })
}

function issueTexts(understanding?: Record<string, any>) {
  if (!understanding) return []
  const texts: string[] = []
  if (understanding.missing_slots?.length) {
    texts.push(`缺失：${understanding.missing_slots.join('、')}`)
  }
  for (const [label, issues] of [
    ['歧义', understanding.ambiguous_slots],
    ['低置信', understanding.low_confidence_slots],
    ['冲突', understanding.conflict_slots],
  ] as Array<[string, Array<Record<string, any>> | undefined]>) {
    for (const issue of issues || []) {
      texts.push(`${label}：${issue.reason || issue.slot}`)
    }
  }
  return texts
}

onMounted(async () => {
  const record = props.record
  if (!record?.id) return
  if ((record.agentic_trace || []).length) return
  // 等待澄清的记录由回答组件的恢复逻辑负责拉取 trace，这里只回填已结束的历史记录。
  if (record.status === 'waiting_user') return
  try {
    const trace = await agenticQuestionApi.trace(record.id)
    record.agentic_trace = trace.events
    expanded.value = false
  } catch {
    // 历史 trace 拉取失败不影响主回答展示。
  }
})
</script>

<template>
  <div v-if="steps.length" class="agentic-live-trace">
    <button type="button" class="trace-header" @click="expanded = !expanded">
      <span class="status-icon" :class="headerStatus">
        <span v-if="headerStatus === 'running'" class="spinner"></span>
        <span v-else-if="headerStatus === 'waiting'" class="pause">⏸</span>
        <span v-else-if="headerStatus === 'failed'" class="cross">✕</span>
        <span v-else class="check">✓</span>
      </span>
      <span class="header-text">{{ headerText }}</span>
      <el-icon class="chevron">
        <icon_up_outlined v-if="expanded" />
        <icon_down_outlined v-else />
      </el-icon>
    </button>
    <div v-show="expanded" class="trace-steps">
      <div v-for="(step, index) in steps" :key="step.key" class="trace-step" :class="step.status">
        <div class="rail">
          <span class="dot" :class="step.status">
            <span v-if="step.status === 'running'" class="spinner small"></span>
          </span>
          <span v-if="index < steps.length - 1" class="line"></span>
        </div>
        <div class="step-body">
          <div class="step-title">
            <span class="title-text">{{ step.title }}</span>
            <span v-if="step.toolName" class="tool-name">{{ step.toolName }}</span>
            <span v-if="step.durationMs !== undefined" class="duration">
              {{ (step.durationMs / 1000).toFixed(1) }}s
            </span>
          </div>
          <div v-if="step.question" class="step-detail">{{ step.question }}</div>
          <div v-if="step.strategy" class="step-detail">
            策略：{{ step.strategy }}
            <span v-if="step.reason" class="muted">（{{ step.reason }}）</span>
          </div>
          <template v-if="step.understanding">
            <div class="step-detail understanding">
              <span v-if="step.understanding.intent" class="intent-tag">
                {{ step.understanding.intent }}
              </span>
              <span v-if="step.understanding.normalized_question" class="muted">
                {{ step.understanding.normalized_question }}
              </span>
            </div>
            <div v-if="slotLabels(step.understanding.slots).length" class="chip-row">
              <span
                v-for="label in slotLabels(step.understanding.slots)"
                :key="label"
                class="slot-chip"
              >
                {{ label }}
              </span>
            </div>
            <div
              v-for="text in issueTexts(step.understanding)"
              :key="text"
              class="step-detail muted"
            >
              {{ text }}
            </div>
          </template>
          <pre v-if="step.sql" class="sql-block">{{ step.sql }}</pre>
          <div v-if="step.rowCount !== undefined" class="step-detail">
            返回 {{ step.rowCount }} 行
            <span v-if="step.fields?.length" class="muted">
              · 字段：{{ step.fields.join('、') }}</span
            >
          </div>
          <div v-if="step.errorMessage" class="step-error">{{ step.errorMessage }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="less">
@primary: var(--ed-color-primary, rgba(28, 186, 144, 1));

.agentic-live-trace {
  margin-top: 8px;
  border: 1px solid rgba(222, 224, 227, 1);
  border-radius: 8px;
  background: rgba(248, 249, 250, 1);
}

.trace-header {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  color: rgba(31, 35, 41, 1);

  .header-text {
    flex: 1;
    text-align: left;
    font-weight: 500;
  }

  .chevron {
    color: rgba(100, 106, 115, 1);
  }
}

.status-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  font-size: 12px;

  &.done .check {
    color: @primary;
  }
  &.failed .cross {
    color: rgba(245, 74, 69, 1);
  }
  &.waiting .pause {
    color: rgba(255, 136, 0, 1);
  }
}

.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--ed-color-primary-1a, rgba(28, 186, 144, 0.2));
  border-top-color: @primary;
  border-radius: 50%;
  animation: trace-spin 0.8s linear infinite;

  &.small {
    width: 8px;
    height: 8px;
    border-width: 1.5px;
  }
}

@keyframes trace-spin {
  to {
    transform: rotate(360deg);
  }
}

.trace-steps {
  padding: 4px 12px 10px 14px;
}

.trace-step {
  display: flex;
  gap: 10px;

  .rail {
    display: flex;
    flex-direction: column;
    align-items: center;
    width: 12px;
    flex-shrink: 0;

    .dot {
      width: 8px;
      height: 8px;
      margin-top: 6px;
      border-radius: 50%;
      background: rgba(187, 191, 196, 1);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;

      &.success {
        background: @primary;
      }
      &.failed {
        background: rgba(245, 74, 69, 1);
      }
      &.waiting {
        background: rgba(255, 136, 0, 1);
      }
      &.running {
        width: 12px;
        height: 12px;
        margin-top: 4px;
        background: transparent;
      }
    }

    .line {
      flex: 1;
      width: 1px;
      min-height: 8px;
      background: rgba(222, 224, 227, 1);
      margin-top: 2px;
    }
  }

  .step-body {
    flex: 1;
    min-width: 0;
    padding-bottom: 10px;

    .step-title {
      display: flex;
      align-items: baseline;
      gap: 8px;
      flex-wrap: wrap;
      font-size: 13px;
      line-height: 20px;

      .title-text {
        color: rgba(31, 35, 41, 1);
        font-weight: 500;
      }

      .tool-name {
        font-size: 12px;
        color: rgba(143, 149, 158, 1);
        font-family: monospace;
      }

      .duration {
        font-size: 12px;
        color: rgba(143, 149, 158, 1);
      }
    }

    .step-detail {
      margin-top: 2px;
      font-size: 12px;
      line-height: 20px;
      color: rgba(100, 106, 115, 1);
      overflow-wrap: anywhere;

      &.understanding {
        display: flex;
        align-items: center;
        gap: 6px;
        flex-wrap: wrap;
      }
    }

    .intent-tag {
      padding: 0 6px;
      border-radius: 4px;
      font-size: 12px;
      line-height: 18px;
      color: @primary;
      background: var(--ed-color-primary-1a, rgba(28, 186, 144, 0.1));
    }

    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      margin-top: 4px;

      .slot-chip {
        padding: 0 6px;
        border-radius: 4px;
        font-size: 12px;
        line-height: 18px;
        color: rgba(100, 106, 115, 1);
        background: rgba(31, 35, 41, 0.06);
      }
    }

    .sql-block {
      margin: 4px 0 0;
      padding: 8px 10px;
      border-radius: 6px;
      background: rgba(31, 35, 41, 0.06);
      font-size: 12px;
      line-height: 18px;
      font-family: monospace;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: rgba(31, 35, 41, 1);
    }

    .step-error {
      margin-top: 2px;
      font-size: 12px;
      line-height: 20px;
      color: rgba(245, 74, 69, 1);
      overflow-wrap: anywhere;
    }
  }

  .muted {
    color: rgba(143, 149, 158, 1);
  }
}
</style>
