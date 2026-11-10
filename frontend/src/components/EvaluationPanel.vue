<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from "vue";
import { Play, Square, Download, ArrowUpRight, ClipboardCheck, LoaderCircle } from "lucide-vue-next";
import { api, stream, openSource } from "../api";
import type { Evaluation, EvaluationDataset, Source } from "../types";
import { useWorkspace } from "../stores/workspace";
const store = useWorkspace();
const savedDataset = localStorage.getItem("medrag.evaluation.dataset");
const dataset = ref(savedDataset && ["zh", "en", "template"].includes(savedDataset) ? savedDataset : "zh"), datasets = ref<EvaluationDataset[]>([]), items = ref<Evaluation[]>([]);
const running = ref(false), loading = ref(false), error = ref(""), expanded = ref<number | null>(null);
const activeQuestion = ref<number | null>(null), savedAt = ref(""), context = ref("");
let controller: AbortController | null = null;
let runScope = "";
const scope = computed(() => [
  "medrag.evaluation.v2", dataset.value, store.health?.embedding_backend,
  store.health?.llm_model, store.health?.answer_mode,
  store.health?.library_revision,
  store.selectedDocuments === null ? "all" : [...store.selectedDocuments].sort((a,b) => a-b).join(","),
  store.documents.map(d => [d.id, d.num_chunks, d.status].join(":")).join(","),
].join("|"));
const selectedSet = computed(() => datasets.value.find(d => d.id === dataset.value));
const finished = computed(() => items.value.filter(i => i.answer !== undefined));
const evaluated = computed(() => finished.value.filter(i => !i.error));
const graded = computed(() => evaluated.value.filter(i => !i.expected_refusal && !i.refused && i.judgment));
const accuracy = computed(() => graded.value.length
  ? Math.round(100 * graded.value.filter(i => i.judgment === "correct").length / graded.value.length) + "%"
  : "待核验");
const retrieval = computed(() => {
  const observed = items.value.filter(i => typeof i.retrieval_hit === "boolean");
  return observed.length ? observed.filter(i => i.retrieval_hit).length + "/" + observed.length : "—";
});
const refusal = computed(() => {
  const observed = evaluated.value.filter(i => i.expected_refusal);
  return observed.length ? observed.filter(i => i.refused).length + "/" + observed.length : "—";
});
const scopeLabel = computed(() => store.selectedDocuments === null ? "全部教材" : "已选 " + store.selectedDocuments.length + " 本教材");
function save() {
  savedAt.value = new Date().toISOString();
  try {
    localStorage.setItem(runScope || scope.value, JSON.stringify({
      items: items.value, saved_at: savedAt.value, context: context.value,
    }));
  } catch { error.value = "浏览器存储空间不足，结果保留在当前页面，请及时导出。"; }
}
async function load() {
  if (running.value) return;
  loading.value = true;
  error.value = "";
  expanded.value = null;
  runScope = "";
  const key = scope.value;
  try {
    const [sets, questions] = await Promise.all([
      api<{items: EvaluationDataset[]}>("/evaluation/datasets"),
      api<{items: Evaluation[]}>("/evaluation/questions?dataset=" + dataset.value),
    ]);
    if (key !== scope.value) return;
    datasets.value = sets.items;
    let saved: {items?: Evaluation[]; saved_at?: string; context?: string} = {};
    try { saved = JSON.parse(localStorage.getItem(key) || "{}"); } catch { /* Ignore malformed saved results. */ }
    items.value = questions.items.map(q => {
      const previous = saved.items?.find(i => i.id === q.id && i.question === q.question && i.expected_file_hash === q.expected_file_hash);
      return {...q, ...previous, available: q.available};
    });
    savedAt.value = saved.saved_at || "";
    context.value = saved.context || "";
  } catch (e) { error.value = (e as Error).message; }
  finally { loading.value = false; }
}
watch(scope, load, {immediate: true});
watch(dataset, value => localStorage.setItem("medrag.evaluation.dataset", value));
onUnmounted(() => controller?.abort());
async function run(ids: number[] = []) {
  running.value = true;
  error.value = "";
  controller = new AbortController();
  runScope = scope.value;
  context.value = store.mode + " · " + scopeLabel.value;
  items.value = items.value.map(item => ids.length && !ids.includes(item.id) ? item : {
    id: item.id, question: item.question, category: item.category,
    expected_refusal: item.expected_refusal, expected_pages: item.expected_pages,
    expected_file_hash: item.expected_file_hash, book_title: item.book_title, available: item.available,
  });
  try {
    await stream("/evaluation/run", {
      dataset: dataset.value, question_ids: ids, document_ids: store.selectedDocuments,
    }, controller.signal, (name, data) => {
      if (name === "progress") activeQuestion.value = data.id;
      if (name === "result") {
        const index = items.value.findIndex(i => i.id === data.id);
        if (index >= 0) items.value[index] = data;
        save();
      }
    });
  } catch (e) {
    if ((e as Error).name !== "AbortError") error.value = (e as Error).message;
  } finally {
    running.value = false;
    activeQuestion.value = null;
    controller = null;
    if (runScope !== scope.value) await load();
    runScope = "";
  }
}
function grade(item: Evaluation, judgment: "correct" | "wrong") { item.judgment = judgment; save(); }
async function open(source: Source) {
  try { await openSource(source); } catch (e) { error.value = (e as Error).message; }
}
function download() {
  const blob = new Blob([JSON.stringify({
    created_at: new Date().toISOString(), dataset: dataset.value, context: context.value,
    note: "检索原页命中不等于生成答案准确；引用准确率仅统计人工核验的已生成回答。",
    items: items.value,
  }, null, 2)], {type:"application/json"});
  const url = URL.createObjectURL(blob), anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "medrag-evaluation-" + dataset.value + ".json";
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function status(item: Evaluation) {
  if (activeQuestion.value === item.id) return "测试中";
  if (item.error) return "需处理";
  if (item.answer === undefined) return item.available === false ? "教材未就绪" : "待测试";
  if (item.refused) return item.expected_refusal ? "正确拒答" : "已拒答";
  return item.judgment === "correct" ? "已核验" : item.judgment === "wrong" ? "存在错误" : "待核验";
}
</script>
<template>
  <div class="evaluation-panel">
    <div class="eyebrow"><ClipboardCheck :size="16" />质量评估</div>
    <h1>让每一处引用，经得起核对。</h1>
    <p class="muted">选一套与资料匹配的题目，运行问答后回到原页核验。检索命中与回答正确性分别记录。</p>
    <div class="dataset-picker">
      <label for="evaluation-dataset">评估题集</label>
      <select id="evaluation-dataset" v-model="dataset" :disabled="running || loading">
        <option v-for="set in datasets" :key="set.id" :value="set.id">{{ set.title }} · {{ set.count }} 题</option>
      </select>
      <p>{{ selectedSet?.description }}</p>
    </div>
    <div class="eval-metrics">
      <div><small>测试完成</small><strong>{{ finished.length }}<span> / {{ items.length }}</span></strong></div>
      <div><small>检索原页命中</small><strong>{{ retrieval }}</strong></div>
      <div><small>引用准确率 · 人工核验</small><strong>{{ accuracy }}</strong><small>{{ graded.length }} 题已核验</small></div>
      <div><small>资料外问题拒答</small><strong>{{ refusal }}</strong></div>
    </div>
    <div class="eval-toolbar">
      <span>{{ running ? context : store.mode + " · " + scopeLabel }}</span>
      <button class="text-button" :disabled="!finished.length" @click="download"><Download :size="15" />导出结果</button>
      <button v-if="running" class="primary-button" @click="controller?.abort()"><Square :size="14" />停止评估</button>
      <button v-else class="primary-button" :disabled="loading || !items.length || !store.connected || store.generating || store.task?.status === 'processing'" @click="run()"><Play :size="14" />运行 {{ items.length }} 条测试</button>
    </div>
    <div v-if="running" class="eval-running" role="status"><LoaderCircle :size="14" class="spinning" />正在测试第 {{ activeQuestion || 1 }} 题，已完成的结果会自动保留。</div>
    <div v-if="error" class="error-alert" role="alert">{{ error }}</div>
    <div class="eval-note">预期页码来自独立核对的原 PDF，仅用于检索检查；它不代表回答中的每条结论都正确。原文摘录结果不计入 DeepSeek 生成质量。</div>
    <p v-if="savedAt" class="evaluation-saved">上次保存：{{ new Date(savedAt).toLocaleString("zh-CN") }} · {{ context }}。不同题集和检索范围分别保存。</p>
    <div v-if="loading" class="muted">正在读取题集…</div>
    <div class="eval-list">
      <article v-for="item in items" :key="item.id" class="eval-row">
        <button class="eval-row-title" :aria-expanded="expanded === item.id" @click="expanded = expanded === item.id ? null : item.id">
          <span class="eval-number">{{ String(item.id).padStart(2,"0") }}</span>
          <span>{{ item.question }}<small>{{ item.category }}</small></span>
          <span class="eval-result" :class="{ passed: item.judgment === 'correct' || (item.expected_refusal && item.refused) }">{{ status(item) }}</span>
        </button>
        <div v-if="expanded === item.id" class="eval-detail">
          <p v-if="item.expected_pages" class="eval-expectation">标准原页：{{ item.book_title }} · PDF {{ item.expected_pages.join("、") }} 页<span v-if="typeof item.retrieval_hit === 'boolean'"> · {{ item.retrieval_hit ? "检索已命中" : "检索未命中" }}</span></p>
          <p v-if="item.error" class="error-alert">{{ item.error }}</p>
          <pre>{{ item.answer || "运行评估后显示回答。" }}</pre>
          <button v-for="source in item.sources" :key="source.index" class="text-button" @click="open(source)">[{{ source.index }}] {{ source.title }} · PDF p.{{ source.page_start }}<ArrowUpRight :size="14" /></button>
          <div v-if="item.answer && !item.expected_refusal && !item.error && !item.refused && item.metrics?.mode === 'deepseek'" class="grade-actions">
            <span>每条结论均被对应原页支持？</span>
            <button :class="{active:item.judgment === 'correct'}" @click="grade(item,'correct')">引用准确</button>
            <button :class="{active:item.judgment === 'wrong'}" @click="grade(item,'wrong')">存在错误</button>
          </div>
          <div class="eval-detail-footer">
            <small v-if="item.duration_ms" class="muted">首段 {{ item.first_token_ms ?? "—" }} ms · 总耗时 {{ item.duration_ms }} ms</small>
            <button class="text-button" :disabled="running || !store.connected || store.generating || store.task?.status === 'processing'" @click="run([item.id])"><Play :size="12" />只测此题</button>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>
