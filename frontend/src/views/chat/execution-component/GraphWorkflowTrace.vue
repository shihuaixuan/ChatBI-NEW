<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  graphWorkflowApi,
  type GraphPendingInteraction,
  type GraphTraceResponse,
} from '@/api/graph-workflow'
import GraphWorkflowProgress from './GraphWorkflowProgress.vue'
import GraphWorkflowTechnicalTrace from './GraphWorkflowTechnicalTrace.vue'

const props = withDefaults(
  defineProps<{
    runId?: string
    refreshKey?: number
    pendingInteraction?: GraphPendingInteraction | null
  }>(),
  {
    runId: undefined,
    refreshKey: 0,
    pendingInteraction: undefined,
  }
)

const trace = ref<GraphTraceResponse>()
const loading = ref(false)

async function loadTrace() {
  if (!props.runId) return
  loading.value = true
  try {
    trace.value = await graphWorkflowApi.trace(props.runId)
  } finally {
    loading.value = false
  }
}

onMounted(loadTrace)
watch(() => props.refreshKey, loadTrace)
watch(() => props.runId, loadTrace)
</script>

<template>
  <section v-if="runId || loading" class="graph-trace">
    <GraphWorkflowProgress
      :trace="trace"
      :pending-interaction="pendingInteraction"
      :loading="loading"
      @refresh="loadTrace"
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
