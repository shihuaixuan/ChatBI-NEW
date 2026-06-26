<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  graphWorkflowApi,
  type GraphEventResponse,
  type GraphPendingInteraction,
  type GraphTraceResponse,
} from '@/api/graph-workflow'
import GraphWorkflowProgress from './GraphWorkflowProgress.vue'
import GraphWorkflowTechnicalTrace from './GraphWorkflowTechnicalTrace.vue'

const props = withDefaults(
  defineProps<{
    runId?: string
    refreshKey?: number
    events?: GraphEventResponse[]
    pendingInteraction?: GraphPendingInteraction | null
    runtimeLoading?: boolean
  }>(),
  {
    runId: undefined,
    refreshKey: 0,
    events: () => [],
    pendingInteraction: undefined,
    runtimeLoading: false,
  }
)

const trace = ref<GraphTraceResponse>()
const traceLoading = ref(false)

const emits = defineEmits<{
  refreshRun: []
}>()

async function loadTrace() {
  if (!props.runId || props.runtimeLoading) return
  traceLoading.value = true
  try {
    trace.value = await graphWorkflowApi.trace(props.runId)
  } catch (error) {
    // 流式启动时 run 可能刚创建但 trace 暂不可读，等待后续事件或最终刷新即可。
    console.warn(error)
  } finally {
    traceLoading.value = false
  }
}

async function refreshTraceAndRun() {
  await loadTrace()
  emits('refreshRun')
}

onMounted(loadTrace)
watch(() => props.refreshKey, loadTrace)
watch(() => props.runId, loadTrace)
watch(
  () => props.runtimeLoading,
  (runtimeLoading) => {
    if (!runtimeLoading) loadTrace()
  }
)
</script>

<template>
  <section v-if="runId || traceLoading || runtimeLoading" class="graph-trace">
    <GraphWorkflowProgress
      :trace="trace"
      :events="events"
      :pending-interaction="pendingInteraction"
      :loading="runtimeLoading"
      @refresh="refreshTraceAndRun"
    />
    <GraphWorkflowTechnicalTrace :trace="trace" />
  </section>
</template>

<style scoped lang="less">
.graph-trace {
  display: grid;
  gap: 8px;
  margin-top: 8px;
}
</style>
