<script setup lang="ts">
import { computed } from "vue";
import MarkdownIt from "markdown-it";
import hljs from "highlight.js/lib/common";
import { BookOpen, Copy, ThumbsUp, ThumbsDown, Check, AlertCircle } from "lucide-vue-next";
import { ref } from "vue";
import type { Message } from "../types";
const props = defineProps<{
  message: Message;
  selected: boolean;
  mode: string;
}>();
const emit = defineEmits<{
  select: [];
  citation: [index: number];
  feedback: [rating: "up" | "down"];
  error: [message: string];
}>();
const copied = ref(false);
const md: MarkdownIt = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  highlight: (str, lang): string =>
    lang && hljs.getLanguage(lang)
      ? hljs.highlight(str, { language: lang }).value
      : md.utils.escapeHtml(str),
});
md.renderer.rules.blockquote_open = (_tokens, _idx, _options, env) => {
  env.quoteDepth = (env.quoteDepth || 0) + 1;
  return "<blockquote>\n";
};
md.renderer.rules.blockquote_close = (_tokens, _idx, _options, env) => {
  env.quoteDepth -= 1;
  return "</blockquote>\n";
};
md.renderer.rules.text = (tokens, idx, _options, env) => {
  const text = md.utils.escapeHtml(tokens[idx].content);
  // Preserve a textbook's own bracketed references inside exact quoted passages.
  return env.quoteDepth
    ? text
    : text.replace(/\[(\d+)\]/g, (match, index) =>
        props.message.sources.some((s) => s.index === Number(index))
          ? `<button class="inline-citation" data-citation="${index}" aria-label="查看来源 ${index}">${index}</button>`
          : match,
      );
};
md.renderer.rules.link_open = (tokens, idx, options, _env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener noreferrer");
  return self.renderToken(tokens, idx, options);
};
const rendered = computed(() => md.render(props.message.content));
function click(event: MouseEvent) {
  const button = (event.target as HTMLElement).closest("[data-citation]");
  if (button) emit("citation", Number(button.getAttribute("data-citation")));
  else emit("select");
}
async function copy() {
  try {
    await navigator.clipboard.writeText(props.message.content);
    copied.value = true;
    window.setTimeout(() => (copied.value = false), 1600);
  } catch {
    emit("error", "浏览器未允许复制，请手动选择回答文本。");
  }
}
</script>
<template>
  <article v-if="message.role === 'user'" class="user-message">
    <div>{{ message.content }}</div>
    <span class="user-avatar">我</span>
  </article>
  <article v-else class="assistant-message" :class="{ selected }">
    <div class="assistant-avatar"><BookOpen :size="17" /></div>
    <div class="answer-body">
      <div class="answer-byline">
        <strong>MedRAG</strong
        ><span>{{
          message.metrics?.mode === "extractive" ? "原文摘录模式" : message.metrics?.model || mode
        }}</span
        ><span v-if="message.status === 'generating'" class="generating-dot">正在查阅资料</span>
      </div>
      <div v-if="message.content" class="markdown" v-html="rendered" @click="click"></div>
      <div v-else-if="message.status === 'generating'" class="thinking">
        <span></span><span></span><span></span><small>检索原文并核对引用…</small>
      </div>
      <p v-else class="muted">
        {{ message.status === "stopped" ? "生成已停止。" : "本次未生成回答。" }}
      </p>
      <div v-if="message.status === 'stopped' || message.status === 'error'" class="answer-status">
        <AlertCircle :size="13" />{{
          message.status === "stopped" ? "已停止 · 内容可能不完整" : "本次生成未完成"
        }}
      </div>
      <div v-if="message.content && message.status !== 'generating'" class="answer-actions">
        <button v-if="message.sources.length" class="source-action" @click="emit('select')">
          <BookOpen :size="13" />{{ message.sources.length }} 处来源
        </button>
        <span class="action-spacer"></span>
        <button
          class="icon-button"
          :aria-label="copied ? '已复制' : '复制回答'"
          :title="copied ? '已复制' : '复制回答'"
          @click="copy"
        >
          <Check v-if="copied" :size="15" /><Copy v-else :size="15" />
        </button>
        <button
          class="icon-button"
          :class="{ voted: message.feedback === 'up' }"
          aria-label="回答有帮助"
          title="回答有帮助"
          @click="emit('feedback', 'up')"
        >
          <ThumbsUp :size="15" />
        </button>
        <button
          class="icon-button"
          :class="{ voted: message.feedback === 'down' }"
          aria-label="回答需改进"
          title="回答需改进"
          @click="emit('feedback', 'down')"
        >
          <ThumbsDown :size="15" />
        </button>
      </div>
      <small v-if="message.metrics?.usage" class="muted">
        {{ message.metrics.usage.total_tokens }} tokens
        <template v-if="message.metrics.estimated_cost_cny_peak !== undefined">
          · 高峰单价估算 ¥{{ message.metrics.estimated_cost_cny_peak.toFixed(5) }}
          <span :title="message.metrics.cost_note">（以账单为准）</span>
        </template>
      </small>
    </div>
  </article>
</template>
