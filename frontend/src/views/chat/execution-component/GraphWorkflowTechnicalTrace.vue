<script setup lang="ts">
import type { GraphTraceResponse } from '@/api/graph-workflow'

withDefaults(
  defineProps<{
    trace?: GraphTraceResponse
  }>(),
  {
    trace: undefined,
  }
)

function jsonText(value: any) {
  if (value === undefined || value === null || value === '') return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}
</script>

<template>
  <details v-if="trace" class="technical-trace">
    <summary>技术详情</summary>
    <div class="trace-meta">
      <span>run_id: {{ trace.run_id }}</span>
      <span>status: {{ trace.status }}</span>
      <span v-if="trace.current_node">current_node: {{ trace.current_node }}</span>
    </div>
    <article v-for="node in trace.nodes" :key="node.name" class="node-detail">
      <div class="node-head">
        <span>{{ node.name }}</span>
        <span>{{ node.status }}</span>
      </div>
      <div v-if="node.route_reason" class="route-reason">{{ node.route_reason }}</div>
      <pre v-if="jsonText(node.output)" class="json-preview">{{ jsonText(node.output) }}</pre>
    </article>
  </details>
</template>

<style scoped lang="less">
.technical-trace {
  margin-top: 8px;
  border: 1px solid rgba(31, 35, 41, 0.12);
  border-radius: 8px;
  background: rgba(250, 251, 252, 1);
}

summary {
  padding: 8px 12px;
  color: rgba(100, 106, 115, 1);
  cursor: pointer;
  font-size: 13px;
}

.trace-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 0 12px 8px;
  color: rgba(143, 149, 158, 1);
  font-size: 12px;
}

.node-detail {
  padding: 10px 12px;
  border-top: 1px solid rgba(31, 35, 41, 0.08);
}

.node-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: rgba(31, 35, 41, 1);
  font-size: 12px;
  font-weight: 600;
}

.route-reason {
  margin-top: 4px;
  color: rgba(100, 106, 115, 1);
  font-size: 12px;
}

.json-preview {
  max-height: 220px;
  margin: 8px 0 0;
  overflow: auto;
  padding: 8px;
  border-radius: 6px;
  background: rgba(31, 35, 41, 0.04);
  color: rgba(31, 35, 41, 0.86);
  font-size: 12px;
  line-height: 18px;
  white-space: pre-wrap;
}
</style>
