<script setup lang="ts">
import { LoaderCircle, RefreshCw } from "lucide-vue-next";
defineProps<{error?: unknown}>();
function reload() { window.location.reload(); }
</script>
<template>
  <section class="panel-state" :role="error ? 'alert' : 'status'">
    <template v-if="error"><h2>页面需要重新加载</h2><p>应用版本可能已更新，或连接暂时中断。刷新后会恢复已保存的会话和评估结果。</p><button class="primary-button" @click="reload"><RefreshCw :size="14" />刷新页面</button></template>
    <template v-else><LoaderCircle :size="18" class="spinning" /><p>正在打开页面…</p></template>
  </section>
</template>
