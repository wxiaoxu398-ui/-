<script setup lang="ts">
import { onMounted, ref, computed } from "vue";
import { Play, Square, Download, ArrowUpRight, ClipboardCheck } from "lucide-vue-next";
import { api, stream, openSource } from "../api";
import type { Evaluation, Source } from "../types";
import { useWorkspace } from "../stores/workspace";
const store = useWorkspace(),
  items = ref<Evaluation[]>([]),
  running = ref(false),
  error = ref(""),
  expanded = ref<number | null>(null);
let controller: AbortController | null = null;
const evaluated = computed(() => items.value.filter((i) => i.answer !== undefined && !i.error));
const graded = computed(() => evaluated.value.filter((i) => !i.expected_refusal && i.judgment));
const accuracy = computed(() =>
  graded.value.length
    ? Math.round(
        (graded.value.filter((i) => i.judgment === "correct").length / graded.value.length) * 100,
      ) + "%"
    : "待核验",
);
const refusal = computed(() => {
  const results = evaluated.value.filter((i) => i.expected_refusal);
  return results.length ? `${results.filter((i) => i.refused).length}/${results.length}` : "—";
});
function save() {
  localStorage.setItem("medrag.evaluation", JSON.stringify(items.value));
}
onMounted(async () => {
  try {
    const questions = await api<{ items: Evaluation[] }>("/evaluation/questions");
    const saved: Evaluation[] = JSON.parse(localStorage.getItem("medrag.evaluation") || "[]");
    items.value = questions.items.map((q) => ({
      ...q,
      ...saved.find((i) => i.id === q.id),
    }));
  } catch (e) {
    error.value = (e as Error).message;
  }
});
async function run() {
  running.value = true;
  error.value = "";
  controller = new AbortController();
  items.value = items.value.map((i) => ({
    id: i.id,
    question: i.question,
    category: i.category,
    expected_refusal: i.expected_refusal,
  }));
  try {
    await stream(
      "/evaluation/run",
      { document_ids: store.selectedDocuments },
      controller.signal,
      (name, data) => {
        if (name === "result") {
          const idx = items.value.findIndex((i) => i.id === data.id);
          items.value[idx] = data;
          save();
        }
      },
    );
  } catch (e) {
    if ((e as Error).name !== "AbortError") error.value = (e as Error).message;
  } finally {
    running.value = false;
    controller = null;
  }
}
function grade(item: Evaluation, judgment: "correct" | "wrong") {
  item.judgment = judgment;
  save();
}
async function open(source: Source) {
  try {
    await openSource(source);
  } catch (e) {
    error.value = (e as Error).message;
  }
}
function download() {
  const blob = new Blob(
    [
      JSON.stringify(
        {
          created_at: new Date().toISOString(),
          mode: store.mode,
          retrieval: store.health?.embedding_backend,
          items: items.value,
        },
        null,
        2,
      ),
    ],
    { type: "application/json" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "medrag-evaluation.json";
  a.click();
  URL.revokeObjectURL(url);
}
</script>
<template>
  <div class="evaluation-panel">
    <div class="eyebrow"><ClipboardCheck :size="16" />质量评估</div>
    <h1>让每一处引用，经得起核对。</h1>
    <p class="muted">
      20 条测试问题。先核对回答与原页，再标注引用是否准确；未标注的结果不计入准确率。
    </p>
    <div class="eval-metrics">
      <div>
        <small>测试完成</small><strong>{{ evaluated.length }}<span> / 20</span></strong>
      </div>
      <div>
        <small>引用准确率 · 人工核验</small><strong>{{ accuracy }}</strong>
      </div>
      <div>
        <small>教材外问题拒答</small><strong>{{ refusal }}</strong>
      </div>
    </div>
    <div class="eval-toolbar">
      <span
        >{{ store.mode }} ·
        {{
          store.selectedDocuments === null ? "全部教材" : `${store.selectedDocuments.length} 本教材`
        }}</span
      ><button class="text-button" @click="download"><Download :size="15" />导出结果</button
      ><button v-if="running" class="primary-button" @click="controller?.abort()">
        <Square :size="14" />停止评估</button
      ><button
        v-else
        class="primary-button"
        :disabled="!store.connected || store.task?.status === 'processing'"
        @click="run"
      >
        <Play :size="14" />运行 20 条测试
      </button>
    </div>
    <div v-if="error" class="error-alert" role="alert">{{ error }}</div>
    <div class="eval-note">
      这些问题是评估模板，未预设教材或页码。当前资料未覆盖的问题可能被拒答；原文摘录模式结果不能代表
      DeepSeek 生成质量。
    </div>
    <div class="eval-list">
      <article v-for="item in items" :key="item.id" class="eval-row">
        <button class="eval-row-title" @click="expanded = expanded === item.id ? null : item.id">
          <span class="eval-number">{{ String(item.id).padStart(2, "0") }}</span
          ><span
            >{{ item.question }}<small>{{ item.category }}</small></span
          ><span class="eval-result">{{
            item.error
              ? "调用失败"
              : item.answer !== undefined
                ? item.refused
                  ? "已拒答"
                  : "待核验"
                : "待测试"
          }}</span>
        </button>
        <div v-if="expanded === item.id" class="eval-detail">
          <p v-if="item.error" class="error-alert">{{ item.error }}</p>
          <pre>{{ item.answer || "运行评估后显示回答。" }}</pre>
          <button
            v-for="source in item.sources"
            :key="source.index"
            class="text-button"
            @click="open(source)"
          >
            [{{ source.index }}] {{ source.title }} · PDF p.{{ source.page_start
            }}<ArrowUpRight :size="14" />
          </button>
          <div
            v-if="item.answer && !item.expected_refusal && !item.error && !item.refused"
            class="grade-actions"
          >
            <span>回答的每条结论均被对应原页支持？</span
            ><button
              :class="{ active: item.judgment === 'correct' }"
              @click="grade(item, 'correct')"
            >
              引用准确</button
            ><button :class="{ active: item.judgment === 'wrong' }" @click="grade(item, 'wrong')">
              存在错误
            </button>
          </div>
          <small v-if="item.duration_ms" class="muted"
            >首段 {{ item.first_token_ms }} ms · 总耗时 {{ item.duration_ms }} ms</small
          >
          <small v-if="item.metrics?.usage" class="muted">
            · {{ item.metrics.usage.total_tokens }} tokens
            <template v-if="item.metrics.estimated_cost_cny_peak !== undefined">
              · 高峰单价估算 ¥{{ item.metrics.estimated_cost_cny_peak.toFixed(5) }}（以账单为准）
            </template>
          </small>
        </div>
      </article>
    </div>
  </div>
</template>
