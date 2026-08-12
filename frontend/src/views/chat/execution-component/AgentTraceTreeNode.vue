<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AgentTraceNode } from '@/api/agent-chat'

defineOptions({ name: 'AgentTraceTreeNode' })

const props = defineProps<{
  node: AgentTraceNode
  selectedId?: number
}>()

const emit = defineEmits<{
  select: [node: AgentTraceNode]
}>()

const expanded = ref(true)
const hasChildren = computed(() => props.node.children.length > 0)
const tokenTotal = computed(() => {
  const usage = props.node.token_usage
  const total = Number(usage.total_tokens || 0)
  if (total > 0) return total
  return (
    Number(usage.input_tokens || usage.prompt_tokens || 0) +
    Number(usage.output_tokens || usage.completion_tokens || 0)
  )
})

function statusText(status: string) {
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
      partial: '部分缺失',
    }[status] || status
  )
}

function nodeTypeText(nodeType: string) {
  return (
    {
      run: '流程',
      invocation: '调用',
      phase: '阶段',
      llm: '模型',
      tool: '工具',
      validation: '校验',
      projection: '整理',
      persistence: '持久化',
      interaction: '交互',
    }[nodeType] || nodeType
  )
}

function formatDuration(latency?: number | null) {
  if (latency === undefined || latency === null) return '--'
  if (latency < 1000) return `${latency}ms`
  return `${(latency / 1000).toFixed(latency < 10000 ? 2 : 1)}s`
}
</script>

<template>
  <div class="trace-tree-node">
    <div
      class="trace-node-row"
      :class="[
        `status-${node.status}`,
        `type-${node.node_type}`,
        { selected: selectedId === node.id },
      ]"
      @click="emit('select', node)"
    >
      <button
        class="expand-button"
        :class="{ expanded, hidden: !hasChildren }"
        type="button"
        @click.stop="expanded = !expanded"
      >
        ›
      </button>
      <span class="node-type-icon">{{ nodeTypeText(node.node_type).slice(0, 1) }}</span>
      <span class="node-main">
        <span class="node-title">{{ node.display_name }}</span>
        <span class="node-subtitle">
          <span>{{ nodeTypeText(node.node_type) }}</span>
          <span>#{{ node.sequence }}</span>
          <span>{{ formatDuration(node.latency_ms) }}</span>
          <span v-if="tokenTotal > 0">{{ tokenTotal }} Tokens</span>
        </span>
      </span>
      <span class="status-dot"></span>
      <span class="status-label">{{ statusText(node.status) }}</span>
    </div>

    <div v-if="hasChildren && expanded" class="trace-node-children">
      <AgentTraceTreeNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :selected-id="selectedId"
        @select="emit('select', $event)"
      />
    </div>
  </div>
</template>

<style scoped lang="less">
.trace-tree-node {
  position: relative;
}

.trace-node-row {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 58px;
  margin: 2px 6px 2px 0;
  padding: 9px 10px 9px 6px;
  border: 1px solid transparent;
  border-radius: 10px;
  cursor: pointer;
  transition:
    background 0.16s ease,
    border-color 0.16s ease,
    box-shadow 0.16s ease;

  &:hover {
    background: #f7f8fc;
    border-color: #e6e8f0;
  }

  &.selected {
    background: linear-gradient(90deg, #f1f3ff 0%, #f8f9ff 100%);
    border-color: #cfd5ff;
    box-shadow: 0 3px 12px rgba(75, 87, 214, 0.08);
  }
}

.expand-button {
  width: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  color: #7a8194;
  background: transparent;
  font-size: 20px;
  line-height: 18px;
  transform: rotate(0deg);
  transition: transform 0.15s ease;
  cursor: pointer;

  &.expanded {
    transform: rotate(90deg);
  }

  &.hidden {
    visibility: hidden;
  }
}

.node-type-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 30px;
  width: 30px;
  height: 30px;
  margin: 0 9px 0 2px;
  border-radius: 9px;
  color: #fff;
  background: #5965e8;
  font-size: 13px;
  font-weight: 600;
  box-shadow: 0 4px 10px rgba(89, 101, 232, 0.2);
}

.type-llm .node-type-icon {
  background: #202124;
  box-shadow: 0 4px 10px rgba(32, 33, 36, 0.18);
}

.type-tool .node-type-icon {
  background: #7b61df;
}

.type-validation .node-type-icon {
  background: #f09a37;
}

.type-persistence .node-type-icon {
  background: #28a86b;
}

.type-interaction .node-type-icon {
  background: #2477d4;
}

.type-projection .node-type-icon {
  background: #18a4a6;
}

.node-main {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
}

.node-title {
  overflow: hidden;
  color: #252936;
  font-size: 14px;
  font-weight: 500;
  line-height: 21px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-subtitle {
  display: flex;
  gap: 8px;
  margin-top: 2px;
  color: #8a90a0;
  font-size: 11px;
  line-height: 17px;
}

.status-dot {
  flex: 0 0 7px;
  width: 7px;
  height: 7px;
  margin: 0 6px 0 8px;
  border-radius: 50%;
  background: #36ad72;
}

.status-label {
  flex: 0 0 auto;
  color: #667085;
  font-size: 11px;
}

.status-running .status-dot {
  background: #4f65e8;
  box-shadow: 0 0 0 4px rgba(79, 101, 232, 0.12);
  animation: trace-pulse 1.5s ease-in-out infinite;
}

.status-waiting .status-dot {
  background: #e8a23c;
}

.status-failed .status-dot,
.status-rejected .status-dot,
.status-interrupted .status-dot {
  background: #e55353;
}

.status-cancelled .status-dot,
.status-skipped .status-dot {
  background: #9aa0ad;
}

.status-partial .status-dot {
  background: #df7f32;
}

.trace-node-children {
  position: relative;
  margin-left: 25px;
  padding-left: 17px;
  border-left: 1px solid #d9dce7;

  > .trace-tree-node::before {
    position: absolute;
    top: 30px;
    left: -17px;
    width: 16px;
    height: 1px;
    background: #d9dce7;
    content: '';
  }
}

@keyframes trace-pulse {
  50% {
    box-shadow: 0 0 0 6px rgba(79, 101, 232, 0.04);
  }
}
</style>
