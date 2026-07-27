<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  ArrowDown,
  ArrowRight,
  ChatDotRound,
  CircleCheck,
  CircleCheckFilled,
  Clock,
  Loading,
  RefreshRight,
  WarningFilled,
} from '@element-plus/icons-vue'
import type { ChatRecord } from '@/api/chat.ts'
import { agentQuestionApi, type AgentTimelineResponse } from '@/api/agent-chat'
import MdComponent from '@/views/chat/component/MdComponent.vue'
import { buildAgentFlow, type AgentFlowStep } from './agentTimelineProjection'

const props = withDefaults(
  defineProps<{
    record?: ChatRecord
    recordId?: number
    runtimeLoading?: boolean
  }>(),
  {
    record: undefined,
    recordId: undefined,
    runtimeLoading: false,
  }
)

const timeline = ref<AgentTimelineResponse>()
const timelineLoading = ref(false)
const expanded = ref(true)
const openedSteps = reactive<Record<string, boolean>>({})

const currentRecordId = computed(() => props.recordId || props.record?.id)
const liveEvents = computed<Array<Record<string, any>>>(
  () =>
    (props.record?.execution_events as Array<Record<string, any>>) ||
    (props.record?.execution_trace as Array<Record<string, any>>) ||
    []
)
const flow = computed(() => buildAgentFlow(timeline.value, liveEvents.value, props.runtimeLoading))
const budget = computed(() => timeline.value?.budget || {})

async function loadTimeline(force = false) {
  if (!currentRecordId.value) return
  // 实时执行完全依赖 SSE；Timeline 只服务历史恢复与用户主动刷新。
  if (!force && (props.runtimeLoading || props.record?.status === 'waiting_user')) return
  timelineLoading.value = true
  try {
    timeline.value = await agentQuestionApi.timeline(currentRecordId.value)
  } catch (error) {
    console.warn(error)
  } finally {
    timelineLoading.value = false
  }
}

function refreshTimeline() {
  loadTimeline(true)
}

function toggleStep(step: AgentFlowStep) {
  openedSteps[step.key] = !stepOpened(step)
}

function stepOpened(step: AgentFlowStep) {
  if (openedSteps[step.key] !== undefined) return openedSteps[step.key]
  return step.status === 'failed' || step.status === 'running' || step.kind === 'understanding'
}

function statusText(step: AgentFlowStep) {
  if (step.kind === 'thinking') return step.status === 'running' ? '思考中' : '已决策'
  return { running: '进行中', success: '完成', failed: '失败', waiting: '等待补充' }[step.status]
}

function formatDuration(duration?: number) {
  if (duration === undefined || duration === null) return ''
  if (duration < 1000) return `${duration}ms`
  return `${(duration / 1000).toFixed(1)}s`
}

function formatTokens(value: any) {
  const tokens = Number(value || 0)
  if (tokens < 1000) return String(tokens)
  return `${(tokens / 1000).toFixed(tokens >= 10000 ? 1 : 2)}k`
}

function stepSummary(step: AgentFlowStep) {
  if (step.kind === 'thinking') {
    return (
      step.thinking ||
      (step.status === 'running' ? '正在分析当前结果并规划下一步' : '已完成分析并选择下一步')
    )
  }
  if (step.kind === 'understanding') {
    const understanding = step.understanding
    if (!understanding) return step.status === 'running' ? '正在重写问题并识别分析意图' : ''
    return understanding.rewritten_question || understanding.intent_type || ''
  }
  if (step.status === 'failed')
    return conciseError(step.error) || errorCodeText(step.result.error_code)
  if (step.status === 'waiting')
    return step.result.question || step.args.question || '等待用户确认业务口径'
  if (step.status === 'running') return step.thinking || '正在处理'
  switch (step.toolName) {
    case 'search_semantic_assets':
      return semanticSummary(step.result)
    case 'get_dataset_schema':
      return `读取 ${step.result.table_count ?? 0} 张数据表`
    case 'compile_semantic_sql':
    case 'validate_sql':
      return 'SQL 已生成'
    case 'execute_sql':
      return `返回 ${step.result.row_count ?? 0} 行${fieldText(step.result.fields)}`
    case 'clarify':
    case 'understanding_clarification':
      return step.args.question || '业务口径已确认'
    case 'finish':
      return '回答与图表已生成'
    default:
      if (step.result.count !== undefined) return `找到 ${step.result.count} 条结果`
      return '处理完成'
  }
}

function semanticSummary(result: Record<string, any>) {
  const parts: string[] = []
  if (result.status) parts.push(semanticStatusText(result.status))
  if (result.metrics?.length) parts.push(`指标 ${result.metrics.length}`)
  if (result.dimensions?.length) parts.push(`维度 ${result.dimensions.length}`)
  if (result.tables?.length) parts.push(`数据表 ${result.tables.length}`)
  return parts.join(' · ') || '语义检索完成'
}

function semanticStatusText(status: string) {
  return (
    {
      hit: '语义资产已匹配',
      metric_ambiguous: '指标口径待确认',
      dimension_ambiguous: '维度口径待确认',
      missed: '检索完成，未找到可直接使用的指标/维度定义。',
      miss: '检索完成，未找到可直接使用的指标/维度定义。',
    }[status] || status
  )
}

function fieldText(fields?: any[]) {
  return fields?.length ? ` · ${fields.join('、')}` : ''
}

function conciseError(error?: string) {
  if (!error) return ''
  const match = error.match(/\(\d+,\s*["']([^"']+)["']\)/)
  if (match?.[1]) return match[1]
  return error.split('\n')[0].slice(0, 180)
}

function errorCodeText(errorCode?: string) {
  if (!errorCode) return '工具执行失败'
  return `工具执行失败：${errorCode}`
}

function understandingChips(step: AgentFlowStep) {
  const understanding = step.understanding
  if (!understanding) return []
  const chips: string[] = []
  if (understanding.intent_type) chips.push(intentText(understanding.intent_type))
  if (understanding.confidence !== undefined) {
    chips.push(`置信度 ${Math.round(Number(understanding.confidence) * 100)}%`)
  }
  if (understanding.message_type === 'followup') chips.push('追问补全')
  return chips
}

function intentText(intent: string) {
  return (
    {
      metric_query: '指标查询',
      trend_analysis: '趋势分析',
      ranking_analysis: '排行分析',
      comparison_analysis: '对比分析',
      detail_query: '明细查询',
      share_analysis: '占比分析',
      anomaly_analysis: '异常分析',
      unknown: '未知意图',
    }[intent] || intent
  )
}

function validationIssues(step: AgentFlowStep) {
  const validation = step.understanding?.validation
  if (!validation) return []
  return [...(validation.reason_codes || []), ...(validation.clarification_slots || [])]
}

function sqlText(step: AgentFlowStep) {
  return step.result.sql || step.args.sql || ''
}

function filterLabels(step: AgentFlowStep) {
  const filters = Array.isArray(step.args.filters) ? step.args.filters : []
  return filters.map((filter: Record<string, any>) => {
    return `资产 #${filter.asset_id} ${filter.operator || '='} ${formatValue(filter.value)}`
  })
}

function assetLabels(step: AgentFlowStep) {
  const labels: string[] = []
  for (const id of step.args.metric_asset_ids || []) labels.push(`指标 #${id}`)
  for (const id of step.args.dimension_asset_ids || []) labels.push(`维度 #${id}`)
  return labels
}

function optionLabels(step: AgentFlowStep) {
  return (step.args.options || [])
    .map((option: Record<string, any>) => option.label || option.value)
    .filter(Boolean)
}

function formatValue(value: any): string {
  if (value === null || value === undefined) return ''
  if (typeof value !== 'object') return String(value)
  if (value.kind === 'single_date' && value.anchor === 'today') {
    const offset = Number(value.offset_days || 0)
    return offset === 0
      ? '今天'
      : offset === -1
        ? '昨天'
        : `今天${offset > 0 ? '+' : ''}${offset}天`
  }
  if (value.kind === 'relative_range') return `最近 ${value.amount} ${unitText(value.unit)}`
  if (value.kind === 'current_period') return `本${unitText(value.unit)}`
  if (value.kind === 'previous_period') return `上${unitText(value.unit)}`
  if (value.start || value.end_exclusive)
    return `${value.start || ''} ~ ${value.end_exclusive || ''}`
  return JSON.stringify(value)
}

function unitText(unit?: string) {
  return { day: '天', week: '周', month: '月', quarter: '季度', year: '年' }[unit || ''] || unit
}

function rawJson(step: AgentFlowStep) {
  return JSON.stringify({ args: step.args, result: step.result }, null, 2)
}

function hasDetails(step: AgentFlowStep) {
  return Boolean(
    step.kind === 'understanding' ||
    step.error ||
    sqlText(step) ||
    filterLabels(step).length ||
    assetLabels(step).length ||
    optionLabels(step).length ||
    step.args.question ||
    step.args.table_keyword ||
    Object.keys(step.args).length ||
    Object.keys(step.result).length
  )
}

onMounted(loadTimeline)
watch(currentRecordId, () => loadTimeline())
</script>

<template>
  <section v-if="flow.steps.length || runtimeLoading || timelineLoading" class="agent-timeline">
    <button type="button" class="timeline-head" @click="expanded = !expanded">
      <span class="run-status" :class="flow.status">
        <el-icon v-if="flow.status === 'running'" class="spinning"><Loading /></el-icon>
        <el-icon v-else-if="flow.status === 'waiting'"><Clock /></el-icon>
        <el-icon v-else-if="flow.status === 'failed'"><WarningFilled /></el-icon>
        <el-icon v-else><CircleCheckFilled /></el-icon>
      </span>
      <span class="headline">{{ flow.headline }}</span>
      <span v-if="budget.steps" class="run-meta">
        {{ budget.steps }} 步
        <template v-if="budget.tokens_used">
          · {{ formatTokens(budget.tokens_used) }} tokens</template
        >
      </span>
      <el-icon class="head-chevron"><ArrowDown v-if="expanded" /><ArrowRight v-else /></el-icon>
    </button>

    <div v-show="expanded" class="flow-body">
      <article
        v-for="(step, index) in flow.steps"
        :key="step.key"
        class="flow-step"
        :class="[`is-${step.status}`, { 'is-thinking': step.kind === 'thinking' }]"
      >
        <div class="step-rail">
          <span class="step-dot">
            <el-icon v-if="step.kind === 'thinking'"><ChatDotRound /></el-icon>
            <el-icon v-else-if="step.status === 'running'" class="spinning"><Loading /></el-icon>
            <el-icon v-else-if="step.status === 'failed'"><WarningFilled /></el-icon>
            <el-icon v-else-if="step.status === 'waiting'"><Clock /></el-icon>
            <el-icon v-else><CircleCheck /></el-icon>
          </span>
          <span v-if="index < flow.steps.length - 1" class="step-line"></span>
        </div>

        <div class="step-main">
          <button
            type="button"
            class="step-head"
            :disabled="!hasDetails(step)"
            @click="toggleStep(step)"
          >
            <span class="step-title">{{ step.title }}</span>
            <code v-if="step.toolName" class="tool-name">{{ step.toolName }}</code>
            <span v-if="step.latencyMs !== undefined" class="duration">{{
              formatDuration(step.latencyMs)
            }}</span>
            <span class="step-status">{{ statusText(step) }}</span>
            <el-icon v-if="hasDetails(step)" class="step-chevron">
              <ArrowDown v-if="stepOpened(step)" /><ArrowRight v-else />
            </el-icon>
          </button>
          <MdComponent
            v-if="step.kind === 'thinking' && stepSummary(step)"
            class="step-summary thinking-summary"
            :message="stepSummary(step)"
          />
          <div v-else-if="stepSummary(step)" class="step-summary">{{ stepSummary(step) }}</div>

          <div v-if="stepOpened(step)" class="step-detail">
            <template v-if="step.kind === 'understanding'">
              <div v-if="understandingChips(step).length" class="chip-row">
                <span
                  v-for="chip in understandingChips(step)"
                  :key="chip"
                  class="detail-chip intent-chip"
                >
                  {{ chip }}
                </span>
              </div>
              <div v-if="validationIssues(step).length" class="issue-row">
                <span v-for="issue in validationIssues(step)" :key="issue">{{ issue }}</span>
              </div>
            </template>

            <div v-if="assetLabels(step).length || filterLabels(step).length" class="chip-row">
              <span v-for="label in assetLabels(step)" :key="label" class="detail-chip">{{
                label
              }}</span>
              <span
                v-for="label in filterLabels(step)"
                :key="label"
                class="detail-chip filter-chip"
              >
                {{ label }}
              </span>
            </div>
            <div v-if="step.args.table_keyword" class="detail-row">
              <span class="detail-label">数据表</span>{{ step.args.table_keyword }}
            </div>
            <div v-if="step.args.question" class="detail-row">
              <span class="detail-label">澄清问题</span>{{ step.args.question }}
            </div>
            <div v-if="optionLabels(step).length" class="option-list">
              <span v-for="option in optionLabels(step)" :key="option">{{ option }}</span>
            </div>
            <pre v-if="sqlText(step)" class="sql-block">{{ sqlText(step) }}</pre>
            <div v-if="step.error" class="error-block">
              <div class="error-title">{{ conciseError(step.error) }}</div>
              <pre>{{ step.error }}</pre>
            </div>
            <details
              v-if="
                step.kind === 'tool' &&
                (Object.keys(step.args).length || Object.keys(step.result).length)
              "
              class="raw-detail"
            >
              <summary>技术参数</summary>
              <pre>{{ rawJson(step) }}</pre>
            </details>
          </div>
        </div>
      </article>

      <div class="timeline-footer">
        <span v-if="timeline?.run_id">Run #{{ timeline.run_id }}</span>
        <span v-if="flow.failedCount">{{ flow.failedCount }} 次失败后恢复</span>
        <button type="button" class="refresh-btn" :disabled="timelineLoading" @click="refreshTimeline">
          <el-icon :class="{ spinning: timelineLoading }"><RefreshRight /></el-icon>
          刷新
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped lang="less">
@primary: var(--ed-color-primary, rgba(51, 112, 255, 1));
@text: rgba(31, 35, 41, 1);
@secondary: rgba(100, 106, 115, 1);
@muted: rgba(143, 149, 158, 1);
@line: rgba(31, 35, 41, 0.12);
@danger: rgba(216, 57, 49, 1);
@warning: rgba(215, 125, 0, 1);
@running: rgba(51, 112, 255, 1);

.agent-timeline {
  margin-top: 8px;
  overflow: hidden;
  border: 1px solid @line;
  border-radius: 8px;
  background: rgba(248, 249, 250, 1);
}

.timeline-head {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto 18px;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 44px;
  padding: 9px 12px;
  border: 0;
  background: transparent;
  color: @text;
  cursor: pointer;
  text-align: left;
}

.run-status {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: @primary;
  font-size: 17px;

  &.running {
    color: @running;
  }
  &.waiting {
    color: @warning;
  }
  &.failed {
    color: @danger;
  }
}

.headline {
  min-width: 0;
  overflow: hidden;
  font-size: 14px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-meta,
.head-chevron {
  color: @muted;
  font-size: 12px;
}

.flow-body {
  padding: 4px 12px 8px 14px;
  border-top: 1px solid rgba(31, 35, 41, 0.08);
}

.flow-step {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  column-gap: 10px;
}

.step-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.step-dot {
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 20px;
  color: @primary;
  background: rgba(248, 249, 250, 1);
  font-size: 14px;
}

.is-running .step-dot {
  color: @running;
}
.is-thinking .step-dot,
.is-thinking .step-title {
  color: @running;
}
.is-thinking .step-line {
  background: rgba(51, 112, 255, 0.22);
}
.is-waiting .step-dot {
  color: @warning;
}
.is-failed .step-dot {
  color: @danger;
}

.step-line {
  flex: 1;
  width: 1px;
  min-height: 14px;
  background: rgba(51, 112, 255, 0.24);
}

.is-running .step-line {
  background: linear-gradient(
    180deg,
    rgba(51, 112, 255, 0.12),
    rgba(51, 112, 255, 0.72),
    rgba(51, 112, 255, 0.12)
  );
  background-size: 100% 24px;
  animation: running-line 1s linear infinite;
}

.step-main {
  min-width: 0;
  padding-bottom: 11px;
}

.step-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 24px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;

  &:disabled {
    cursor: default;
  }
}

.step-title {
  color: @text;
  font-size: 13px;
  font-weight: 500;
}

.tool-name {
  min-width: 0;
  overflow: hidden;
  color: @muted;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.duration,
.step-status,
.step-chevron {
  flex: 0 0 auto;
  color: @muted;
  font-size: 12px;
}

.step-status {
  margin-left: auto;
}

.is-failed .step-status {
  color: @danger;
}
.is-running .step-status {
  color: @running;
}
.is-waiting .step-status {
  color: @warning;
}

.step-summary,
.detail-row {
  margin-top: 1px;
  color: @secondary;
  font-size: 12px;
  line-height: 19px;
  overflow-wrap: anywhere;
}

.thinking-summary {
  background: transparent;

  :deep(p) {
    margin: 0;
    color: inherit;
    font-size: inherit;
    line-height: inherit;
  }

  :deep(p + p) {
    margin-top: 4px;
  }

  :deep(code) {
    padding: 1px 3px;
    border-radius: 3px;
    background: rgba(51, 112, 255, 0.08);
    color: rgba(36, 91, 219, 1);
    font-size: 11px;
  }
}

.step-detail {
  display: grid;
  gap: 6px;
  margin-top: 6px;
}

.chip-row,
.issue-row,
.option-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.detail-chip,
.issue-row span,
.option-list span {
  max-width: 100%;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(31, 35, 41, 0.05);
  color: @secondary;
  font-size: 11px;
  line-height: 18px;
  overflow-wrap: anywhere;
}

.intent-chip {
  background: rgba(51, 112, 255, 0.09);
  color: rgba(36, 91, 219, 1);
}

.filter-chip {
  background: rgba(51, 112, 255, 0.1);
  color: rgba(36, 91, 219, 1);
}

.issue-row span {
  background: rgba(215, 125, 0, 0.1);
  color: rgba(172, 96, 0, 1);
}

.detail-label {
  display: inline-block;
  min-width: 54px;
  color: @muted;
}

.sql-block,
.error-block pre,
.raw-detail pre {
  box-sizing: border-box;
  width: 100%;
  max-height: 240px;
  margin: 0;
  overflow: auto;
  padding: 8px 10px;
  border: 1px solid rgba(31, 35, 41, 0.1);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.82);
  color: rgba(31, 35, 41, 0.86);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  line-height: 18px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.error-block {
  overflow: hidden;
  border: 1px solid rgba(216, 57, 49, 0.2);
  border-radius: 6px;
  background: rgba(216, 57, 49, 0.05);

  .error-title {
    padding: 7px 9px;
    color: @danger;
    font-size: 12px;
    font-weight: 500;
  }

  pre {
    max-height: 180px;
    border: 0;
    border-top: 1px solid rgba(216, 57, 49, 0.14);
    border-radius: 0;
    background: rgba(255, 255, 255, 0.56);
    color: rgba(137, 42, 36, 1);
  }
}

.raw-detail {
  color: @muted;
  font-size: 11px;

  summary {
    width: fit-content;
    cursor: pointer;
  }

  pre {
    margin-top: 6px;
    background: rgba(31, 35, 41, 0.035);
  }
}

.timeline-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 28px;
  padding-left: 28px;
  border-top: 1px solid rgba(31, 35, 41, 0.07);
  color: @muted;
  font-size: 11px;
}

.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  padding: 4px;
  border: 0;
  background: transparent;
  color: @secondary;
  cursor: pointer;

  &:disabled {
    cursor: default;
    opacity: 0.5;
  }
}

.spinning {
  animation: spin 0.9s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes running-line {
  from {
    background-position: 0 0;
  }
  to {
    background-position: 0 24px;
  }
}

@media (max-width: 640px) {
  .timeline-head {
    grid-template-columns: 20px minmax(0, 1fr) 18px;
  }

  .run-meta {
    display: none;
  }

  .tool-name {
    max-width: 42%;
  }

  .duration {
    display: none;
  }

  .flow-body {
    padding-right: 9px;
    padding-left: 10px;
  }
}
</style>
