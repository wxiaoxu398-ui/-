<script setup lang="ts">
import { computed } from "vue";
import { Check, Search, LoaderCircle, ShieldCheck, ChevronDown } from "lucide-vue-next";
import type { Message } from "../types";
const props = defineProps<{ message: Message }>();
const busy = computed(() => props.message.status === "generating");
const retrieved = computed(() => props.message.metrics?.retrieved_sources ?? props.message.sources.length);
const locating = computed(() => busy.value && props.message.phase === "retrieving");
const completed = computed(() => props.message.status === "complete");
const label = computed(() => locating.value ? "正在查阅教材" : busy.value
  ? props.message.phase === "refusing" ? "资料不足，准备说明" : "正在整理答案并检查引用"
  : props.message.sources.length ? `证据路径 · ${props.message.sources.length} 处引用` : "本次未提供可用引用");
function seconds(ms: number) { return `${(ms / 1000).toFixed(2)} 秒`; }
</script>
<template>
  <details v-if="message.metrics || busy" class="evidence-trace" :open="busy">
    <summary>
      <LoaderCircle v-if="busy" :size="13" class="spinning" /><ShieldCheck v-else :size="13" />
      <span>{{ label }}</span><ChevronDown :size="12" />
    </summary>
    <ol>
      <li :class="{ active: locating }"><Search :size="14" /><div><strong>查阅原文</strong><small>{{ locating ? "在所选教材中检索" : `找到 ${retrieved} 条相关片段` }}<template v-if="message.metrics?.retrieval_ms !== undefined"> · {{ seconds(message.metrics.retrieval_ms) }}</template></small></div></li>
      <li :class="{ active: busy && !locating }"><Check :size="14" /><div><strong>组织答案与格式检查</strong><small>{{ message.metrics?.mode === "extractive" ? "直接展示原文摘录" : message.metrics?.validated_blocks ? `${message.metrics.validated_blocks} 个段落通过引用格式检查` : busy ? "逐段检查后显示" : completed && !message.sources.length ? "资料不足，已拒答" : "未完成生成检查" }}</small></div></li>
      <li><ShieldCheck :size="14" /><div><strong>回到原页核对</strong><small>编号和格式检查不能代替原文的语义核验。</small></div></li>
    </ol>
    <p v-if="message.metrics?.duration_ms !== undefined">首段 {{ message.metrics.first_token_ms == null ? "—" : seconds(message.metrics.first_token_ms) }} · 总耗时 {{ seconds(message.metrics.duration_ms) }}</p>
  </details>
</template>
