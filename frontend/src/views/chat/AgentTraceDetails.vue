<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import {
  agentQuestionApi,
  type AgentTraceNode,
  type AgentTraceNodeDetail,
  type AgentTraceResponse,
} from '@/api/agent-chat'
import { isMobile } from '@/utils/utils'
import AgentTraceTreeNode from './execution-component/AgentTraceTreeNode.vue'

type DetailTab = 'input' | 'output' | 'state' | 'metadata'

const visible = ref(false)
const loading = ref(false)
const detailLoading = ref(false)
const loadError = ref('')
const detailError = ref('')
const drawerSize = ref('min(1180px, calc(100vw - 48px))')
const recordId = ref<number>()
const trace = ref<AgentTraceResponse>()
const selectedNode = ref<AgentTraceNode>()
const selectedDetail = ref<AgentTraceNodeDetail>()
const activeTab = ref<DetailTab>('input')
const detailCache = new Map<number, AgentTraceNodeDetail>()
let refreshTimer: number | undefined

const overview = computed(() => trace.value?.overview)
const selectedPayload = computed(() => {
  if (!selectedDetail.value) return undefined
  if (activeTab.value === 'input') {
    return {
      summary: selectedDetail.value.input_summary,
      detail: selectedDetail.value.input_detail,
    }
  }
  if (activeTab.value === 'output') {
    return {
      summary: selectedDetail.value.output_summary,
      detail: selectedDetail.value.output_detail,
    }
  }
  if (activeTab.value === 'state') return selectedDetail.value.state_diff
  return selectedDetail.value.metadata
})

async function open(currentRecordId: number) {
  recordId.value = currentRecordId
  drawerSize.value = isMobile() ? '100%' : 'min(1180px, calc(100vw - 48px))'
  visible.value = true
  detailCache.clear()
  selectedNode.value = undefined
  selectedDetail.value = undefined
  await loadTrace(true)
}

async function loadTrace(selectFirst: boolean = false) {
  if (!recordId.value) return
  clearRefreshTimer()
  loading.value = true
  loadError.value = ''
  try {
    trace.value = await agentQuestionApi.trace(recordId.value)
    const currentNode = selectedNode.value
      ? findNode(trace.value.tree, selectedNode.value.id)
      : undefined
    if (currentNode) selectedNode.value = currentNode
    if (selectFirst && !selectedNode.value) {
      const firstNode = trace.value.tree[0]
      if (firstNode) await selectNode(firstNode)
    }
    if (['running', 'waiting'].includes(trace.value.overview.status)) {
      refreshTimer = window.setTimeout(() => loadTrace(false), 3000)
    }
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '执行详情加载失败'
  } finally {
    loading.value = false
  }
}

async function selectNode(node: AgentTraceNode) {
  selectedNode.value = node
  activeTab.value = 'input'
  detailError.value = ''
  const cached = detailCache.get(node.id)
  if (cached) {
    selectedDetail.value = cached
    return
  }
  if (!recordId.value) return
  detailLoading.value = true
  selectedDetail.value = undefined
  try {
    const detail = await agentQuestionApi.traceNode(recordId.value, node.id)
    detailCache.set(node.id, detail)
    selectedDetail.value = detail
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '节点详情加载失败'
  } finally {
    detailLoading.value = false
  }
}

function findNode(nodes: AgentTraceNode[], nodeId: number): AgentTraceNode | undefined {
  for (const node of nodes) {
    if (node.id === nodeId) return node
    const child = findNode(node.children, nodeId)
    if (child) return child
  }
  return undefined
}

function formatDuration(durationMs?: number | null) {
  if (durationMs === undefined || durationMs === null) return '--'
  if (durationMs < 1000) return `${durationMs}ms`
  return `${(durationMs / 1000).toFixed(2)}s`
}

function formatDateTime(value?: string | null) {
  if (!value) return '--'
  return new Date(value).toLocaleString()
}

function formatJson(value: unknown) {
  if (value === undefined || value === null) return '无数据'
  if (typeof value === 'object' && Object.keys(value as object).length === 0) return '{}'
  return JSON.stringify(value, null, 2)
}

function statusText(status?: string) {
  if (!status) return '--'
  return (
    {
      running: '执行中',
      succeeded: '成功',
      failed: '失败',
      rejected: '已拒绝',
      interrupted: '已中断',
      waiting: '等待用户',
      cancelled: '已取消',
      skipped: '已跳过',
      partial: '部分数据缺失',
    }[status] || status
  )
}

function clearRefreshTimer() {
  if (refreshTimer !== undefined) {
    window.clearTimeout(refreshTimer)
    refreshTimer = undefined
  }
}

function closeDrawer() {
  clearRefreshTimer()
  trace.value = undefined
  selectedNode.value = undefined
  selectedDetail.value = undefined
}

onBeforeUnmount(clearRefreshTimer)

defineExpose({ open })
</script>

<template>
  <el-drawer
    v-model="visible"
    title="执行详情"
    destroy-on-close
    modal-class="agent-trace-details"
    :size="drawerSize"
    @closed="closeDrawer"
  >
    <div v-if="loading && !trace" class="trace-loading">
      <div class="loading-mark"></div>
      <span>正在读取执行链路...</span>
    </div>

    <div v-else-if="loadError && !trace" class="trace-empty trace-error">
      <div class="empty-icon">!</div>
      <div>{{ loadError }}</div>
      <el-button type="primary" link @click="loadTrace(true)">重新加载</el-button>
    </div>

    <template v-else-if="trace && overview">
      <section class="overview-section">
        <div class="section-heading">
          <div>
            <h3>执行概览</h3>
            <span>Run #{{ overview.run_id }} · {{ formatDateTime(overview.started_at) }}</span>
          </div>
          <button
            class="refresh-button"
            type="button"
            :disabled="loading"
            @click="loadTrace(false)"
          >
            ↻ 刷新
          </button>
        </div>

        <div class="overview-grid">
          <div class="overview-card token-card">
            <span class="overview-icon">T</span>
            <span class="overview-content">
              <span class="overview-label">消耗 Tokens</span>
              <strong>{{ overview.total_tokens.toLocaleString() }}</strong>
              <small>输入 {{ overview.input_tokens }} / 输出 {{ overview.output_tokens }}</small>
            </span>
          </div>
          <div class="overview-card duration-card">
            <span class="overview-icon">◷</span>
            <span class="overview-content">
              <span class="overview-label">整体耗时</span>
              <strong>{{ formatDuration(overview.duration_ms) }}</strong>
              <small>{{ formatDateTime(overview.finished_at) }}</small>
            </span>
          </div>
          <div class="overview-card node-card">
            <span class="overview-icon">⌘</span>
            <span class="overview-content">
              <span class="overview-label">执行节点</span>
              <strong>{{ overview.node_count }}</strong>
              <small
                >失败 {{ overview.failed_node_count }} / 等待
                {{ overview.waiting_node_count }}</small
              >
            </span>
          </div>
          <div class="overview-card status-card" :class="`status-${overview.status}`">
            <span class="overview-icon">●</span>
            <span class="overview-content">
              <span class="overview-label">当前状态</span>
              <strong>{{ statusText(overview.status) }}</strong>
              <small>最新序号 #{{ overview.last_sequence }}</small>
            </span>
          </div>
        </div>

        <div v-if="overview.partial" class="partial-notice">
          Trace 采集过程中存在节点写入失败，当前调用树会保留已成功记录的部分。
        </div>
      </section>

      <section class="trace-workspace">
        <div class="tree-panel">
          <div class="panel-heading">
            <div>
              <h3>调用树</h3>
              <span>点击节点查看输入、输出和状态变化</span>
            </div>
          </div>
          <div v-if="trace.tree.length" class="tree-scroll">
            <AgentTraceTreeNode
              v-for="root in trace.tree"
              :key="root.id"
              :node="root"
              :selected-id="selectedNode?.id"
              @select="selectNode"
            />
          </div>
          <div v-else class="trace-empty compact">
            <div class="empty-icon">⌁</div>
            <span>该记录暂无 Trace 节点</span>
          </div>
        </div>

        <div class="detail-panel">
          <div v-if="!selectedNode" class="trace-empty">
            <div class="empty-icon">⌘</div>
            <span>请选择一个节点查看详情</span>
          </div>
          <template v-else>
            <div class="detail-header">
              <div class="detail-title-row">
                <span class="detail-type">{{ selectedNode.node_type }}</span>
                <h3>{{ selectedNode.display_name }}</h3>
                <span class="detail-status" :class="`status-${selectedNode.status}`">
                  {{ statusText(selectedNode.status) }}
                </span>
              </div>
              <div class="detail-facts">
                <span>序号 #{{ selectedNode.sequence }}</span>
                <span>耗时 {{ formatDuration(selectedNode.latency_ms) }}</span>
                <span>开始 {{ formatDateTime(selectedNode.started_at) }}</span>
              </div>
              <div v-if="selectedNode.error" class="node-error">
                <strong>{{
                  selectedNode.error_code || selectedNode.error_category || '执行失败'
                }}</strong>
                <span>{{ selectedNode.error }}</span>
              </div>
            </div>

            <div class="detail-tabs">
              <button
                v-for="tab in [
                  { key: 'input', label: '输入' },
                  { key: 'output', label: '输出' },
                  { key: 'state', label: '状态变化' },
                  { key: 'metadata', label: '运行信息' },
                ]"
                :key="tab.key"
                type="button"
                :class="{ active: activeTab === tab.key }"
                @click="activeTab = tab.key as DetailTab"
              >
                {{ tab.label }}
              </button>
            </div>

            <div v-if="detailLoading" class="trace-loading detail-loading">
              <div class="loading-mark"></div>
              <span>正在读取节点详情...</span>
            </div>
            <div v-else-if="detailError" class="trace-empty trace-error compact">
              <div class="empty-icon">!</div>
              <span>{{ detailError }}</span>
              <el-button type="primary" link @click="selectNode(selectedNode)">重新加载</el-button>
            </div>
            <div v-else-if="selectedDetail" class="json-content">
              <template v-if="activeTab === 'input' || activeTab === 'output'">
                <div class="json-block">
                  <div class="json-heading">摘要</div>
                  <pre>{{ formatJson((selectedPayload as any)?.summary) }}</pre>
                </div>
                <div class="json-block">
                  <div class="json-heading">
                    完整脱敏详情
                    <span v-if="activeTab === 'input' && !selectedNode.has_input_detail"
                      >未单独存档</span
                    >
                    <span v-if="activeTab === 'output' && !selectedNode.has_output_detail"
                      >未单独存档</span
                    >
                  </div>
                  <pre>{{ formatJson((selectedPayload as any)?.detail) }}</pre>
                </div>
              </template>
              <div v-else class="json-block">
                <div class="json-heading">
                  {{ activeTab === 'state' ? '前后状态' : 'Trace 元数据' }}
                </div>
                <pre>{{ formatJson(selectedPayload) }}</pre>
              </div>
              <div v-if="activeTab === 'metadata'" class="trace-identifiers">
                <span>Trace ID：{{ selectedDetail.trace_id || '--' }}</span>
                <span>Span ID：{{ selectedDetail.span_id || '--' }}</span>
              </div>
            </div>
          </template>
        </div>
      </section>
    </template>
  </el-drawer>
</template>

<style lang="less">
.agent-trace-details {
  .el-drawer {
    background: #fbfbfd;
  }

  .el-drawer__header {
    height: 68px;
    margin: 0;
    padding: 0 28px;
    border-bottom: 1px solid #eceef3;
    color: #202431;
    font-size: 20px;
    font-weight: 600;
  }

  .el-drawer__body {
    padding: 24px 28px 28px;
  }

  h3 {
    margin: 0;
    color: #252936;
    font-size: 16px;
    line-height: 24px;
  }

  .section-heading,
  .panel-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;

    > div {
      display: flex;
      flex-direction: column;
    }

    span {
      margin-top: 2px;
      color: #8a90a0;
      font-size: 12px;
    }
  }

  .refresh-button {
    padding: 7px 12px;
    border: 1px solid #dfe2eb;
    border-radius: 8px;
    color: #596174;
    background: #fff;
    cursor: pointer;

    &:hover {
      border-color: #aeb6e9;
      color: #4e5bd4;
    }
  }

  .overview-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
  }

  .overview-card {
    display: flex;
    align-items: center;
    min-height: 94px;
    padding: 15px;
    border: 1px solid #e2e4ec;
    border-radius: 14px;
    background: #fff;
    box-shadow: 0 5px 16px rgba(30, 35, 64, 0.035);
  }

  .overview-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 42px;
    width: 42px;
    height: 42px;
    margin-right: 12px;
    border-radius: 12px;
    color: #5965e8;
    background: #eef0ff;
    font-size: 18px;
    font-weight: 600;
  }

  .duration-card .overview-icon {
    color: #2975d1;
    background: #eaf3ff;
  }

  .node-card .overview-icon {
    color: #8a5ad9;
    background: #f2ebff;
  }

  .status-card .overview-icon {
    color: #2a9a64;
    background: #e8f7ef;
  }

  .status-card.status-failed .overview-icon,
  .status-card.status-interrupted .overview-icon {
    color: #d84b4b;
    background: #fff0f0;
  }

  .status-card.status-waiting .overview-icon,
  .status-card.status-partial .overview-icon {
    color: #d47a26;
    background: #fff4e8;
  }

  .overview-content {
    display: flex;
    min-width: 0;
    flex-direction: column;

    strong {
      margin-top: 2px;
      color: #202431;
      font-size: 21px;
      line-height: 27px;
    }

    small {
      overflow: hidden;
      margin-top: 1px;
      color: #9297a6;
      font-size: 11px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .overview-label {
    color: #6d7382;
    font-size: 12px;
  }

  .partial-notice {
    margin-top: 10px;
    padding: 9px 12px;
    border: 1px solid #f0d7b7;
    border-radius: 8px;
    color: #9a622e;
    background: #fff8ef;
    font-size: 12px;
  }

  .trace-workspace {
    display: grid;
    min-height: 520px;
    margin-top: 22px;
    border: 1px solid #e0e3eb;
    border-radius: 16px;
    grid-template-columns: minmax(360px, 42%) minmax(0, 1fr);
    background: #fff;
    overflow: hidden;
  }

  .tree-panel,
  .detail-panel {
    min-width: 0;
    padding: 18px;
  }

  .tree-panel {
    border-right: 1px solid #e8eaf0;
    background: #fdfdfe;
  }

  .tree-scroll,
  .json-content {
    max-height: calc(100vh - 360px);
    overflow: auto;
  }

  .detail-panel {
    position: relative;
    display: flex;
    flex-direction: column;
  }

  .detail-header {
    padding: 2px 2px 14px;
    border-bottom: 1px solid #eceef3;
  }

  .detail-title-row {
    display: flex;
    align-items: center;
    gap: 9px;
  }

  .detail-type,
  .detail-status {
    padding: 3px 8px;
    border-radius: 6px;
    color: #535fd2;
    background: #eef0ff;
    font-size: 11px;
    text-transform: uppercase;
  }

  .detail-status {
    margin-left: auto;
    color: #218957;
    background: #eaf7f0;

    &.status-failed,
    &.status-interrupted,
    &.status-rejected {
      color: #c74242;
      background: #fff0f0;
    }

    &.status-waiting,
    &.status-partial {
      color: #bc6d27;
      background: #fff4e8;
    }
  }

  .detail-facts {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 16px;
    margin-top: 9px;
    color: #848a99;
    font-size: 12px;
  }

  .node-error {
    display: flex;
    margin-top: 12px;
    padding: 9px 11px;
    border-radius: 8px;
    flex-direction: column;
    color: #bd4141;
    background: #fff2f2;
    font-size: 12px;

    span {
      margin-top: 3px;
      word-break: break-word;
    }
  }

  .detail-tabs {
    display: flex;
    gap: 4px;
    margin: 14px 0;
    padding: 4px;
    border-radius: 9px;
    background: #f2f3f7;

    button {
      flex: 1;
      padding: 7px 6px;
      border: 0;
      border-radius: 7px;
      color: #6f7585;
      background: transparent;
      font-size: 12px;
      cursor: pointer;

      &.active {
        color: #4653ce;
        background: #fff;
        box-shadow: 0 2px 7px rgba(35, 42, 85, 0.1);
      }
    }
  }

  .json-block {
    margin-bottom: 12px;
  }

  .json-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
    color: #5c6272;
    font-size: 12px;
    font-weight: 500;

    span {
      color: #9ba0ad;
      font-weight: 400;
    }
  }

  pre {
    min-height: 52px;
    margin: 0;
    padding: 12px 14px;
    border: 1px solid #e4e7ee;
    border-radius: 10px;
    color: #353a48;
    background: #f8f9fb;
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
    font-size: 12px;
    line-height: 19px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .trace-identifiers {
    display: flex;
    margin-top: 8px;
    gap: 8px 18px;
    flex-wrap: wrap;
    color: #858b99;
    font-family: monospace;
    font-size: 11px;
  }

  .trace-loading,
  .trace-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 360px;
    gap: 10px;
    flex-direction: column;
    color: #8b91a0;
    font-size: 13px;
  }

  .trace-loading {
    flex-direction: row;
  }

  .trace-loading.detail-loading {
    min-height: 220px;
  }

  .trace-empty.compact {
    min-height: 210px;
  }

  .trace-error {
    color: #be5151;
  }

  .empty-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 38px;
    height: 38px;
    border-radius: 12px;
    color: #7e85a0;
    background: #f0f2f7;
    font-size: 18px;
  }

  .loading-mark {
    width: 20px;
    height: 20px;
    border: 2px solid #d8dcf8;
    border-top-color: #5965e8;
    border-radius: 50%;
    animation: trace-loading 0.8s linear infinite;
  }

  @keyframes trace-loading {
    to {
      transform: rotate(360deg);
    }
  }

  @media (max-width: 900px) {
    .el-drawer__body {
      padding: 18px;
    }

    .overview-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .trace-workspace {
      grid-template-columns: 1fr;
    }

    .tree-panel {
      border-right: 0;
      border-bottom: 1px solid #e8eaf0;
    }

    .tree-scroll,
    .json-content {
      max-height: 420px;
    }
  }
}
</style>
