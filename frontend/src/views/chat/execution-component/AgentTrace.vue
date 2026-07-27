<script setup lang="ts">
import AgentTimeline from './AgentTimeline.vue'
</script>

<template>
  <!-- 兼容旧组件名称；新代码统一使用 AgentTimeline。 -->
  <AgentTimeline v-bind="$attrs" />
</template>
