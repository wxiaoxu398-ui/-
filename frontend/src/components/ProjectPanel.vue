<script setup lang="ts">
import { computed } from "vue";
import { BookOpen, ArrowUpRight, ShieldCheck, Layers, FileCheck, ExternalLink, ArrowRight } from "lucide-vue-next";
import { useWorkspace } from "../stores/workspace";
const store = useWorkspace();
const emit = defineEmits<{navigate: [view: "chat" | "evaluation" | "project"]; settings: []}>();
const ready = computed(() => store.documents.filter(doc => doc.status === "ready"));
const pages = computed(() => ready.value.reduce((total, doc) => total + doc.num_pages, 0));
const steps = [
  {label: "导入与解析", description: "保留 PDF 物理页码，记录资料来源与许可。"},
  {label: "混合检索", description: "BGE 向量与 BM25 词法检索，通过 RRF 融合。"},
  {label: "证据限定生成", description: "携带本轮原文与对话上下文，逐段检查引用格式。"},
  {label: "原页核验", description: "按文件指纹打开原页，由读者核对结论。"},
];
function safeUrl(value: string) {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : undefined; }
  catch { return undefined; }
}
</script>
<template>
  <div class="project-panel">
    <div class="project-hero">
      <div class="eyebrow"><BookOpen :size="15" />MEDRAG · 医学教材学习助手</div>
      <h1>读懂知识，也读懂依据。</h1>
      <p>面向医学生的教材阅读与复习工作台。把提问、知识梳理、自测和原文核对放在同一个学习过程里。</p>
      <div class="project-actions">
        <button class="primary-button" @click="emit('navigate','chat')">开始教材学习<ArrowUpRight :size="15" /></button>
        <button class="text-button" @click="emit('navigate','evaluation')">查看质量评估<ArrowRight :size="15" /></button>
      </div>
      <div class="project-stats">
        <div><strong>{{ ready.length }}</strong><span>份资料已入库</span></div>
        <div><strong>{{ pages.toLocaleString() }}</strong><span>页可追溯原文</span></div>
        <div><strong>{{ (store.health?.num_chunks || 0).toLocaleString() }}</strong><span>个检索片段</span></div>
      </div>
    </div>
    <section class="project-section">
      <div class="project-section-heading"><span>01 / 学习体验</span><h2>答案之后，还能继续学。</h2></div>
      <div class="project-feature-grid">
        <article><BookOpen :size="21" /><h3>用教材解释概念</h3><p>针对问题组织答案，每个实质性结论附来源编号；点击即可核对片段与原页。</p></article>
        <article><Layers :size="21" /><h3>把原文整理成复习材料</h3><p>切换知识梳理或自测练习，继续沿用教材证据；学习会话可以导出为 Markdown 笔记。</p></article>
        <article><ShieldCheck :size="21" /><h3>知道资料的边界</h3><p>相关资料不足时明确拒答。保留生成过程、来源、耗时与用量，方便检查结果。</p></article>
      </div>
    </section>
    <section class="project-section">
      <div class="project-section-heading"><span>02 / 技术路径</span><h2>从一页原文，到一条可核对的回答。</h2></div>
      <ol class="architecture-flow"><li v-for="(step,index) in steps" :key="step.label"><b>{{ String(index+1).padStart(2,'0') }}</b><strong>{{ step.label }}</strong><p>{{ step.description }}</p></li></ol>
      <p class="project-runtime">当前检索：{{ store.health?.embedding_backend === 'lexical' ? 'BM25 轻量模式' : 'BGE + BM25' }} · 生成：{{ store.health?.llm_configured ? store.health.llm_model : '原文摘录模式' }} · 会话与索引保存在本机。</p>
      <button v-if="!store.health?.llm_configured" class="text-button" @click="emit('settings')">配置 API Key，启用生成式学习<ArrowUpRight :size="13" /></button>
    </section>
    <section class="project-section">
      <div class="project-section-heading"><span>03 / 资料与许可</span><h2>清楚知道，正在读哪一份资料。</h2></div>
      <p class="project-description">资料由用户导入。中文资料包括专业学习手册与心理健康教育指南，不能视作国内医学本科统编教材；许可按每份原书核对。</p>
      <div class="project-book-list">
        <article v-for="doc in store.documents" :key="doc.id"><FileCheck :size="18" /><div><strong>{{ doc.title }}</strong><small>{{ doc.author || '作者待补充' }} · {{ doc.num_pages }} 页 · {{ doc.status === 'ready' ? '已入库' : '尚未就绪' }}</small><p>{{ doc.license || '许可证待补充' }}</p></div><a v-if="safeUrl(doc.source_url)" :href="safeUrl(doc.source_url)" target="_blank" rel="noopener noreferrer" :aria-label="'查看资料官网：' + doc.title"><ExternalLink :size="15" /></a></article>
        <p v-if="!store.documents.length" class="muted">文档库为空，可从学习页面导入有使用权限的 PDF。</p>
      </div>
    </section>
    <section class="project-boundary">
      <ShieldCheck :size="21" /><div><h2>学习工具的适用边界</h2><p>不提供诊断、处方、治疗建议或临床决策。有效的引用编号、检索命中与引用语义准确是不同的检查；回答仍需回到教材核对。</p><p>DeepSeek 问答会把问题、最近的上下文和检索片段发送给模型服务；请使用适合该用途的学习资料。</p></div>
    </section>
    <footer class="project-reference">参照 <a href="https://www.boxuegu.com/matchTrack/detail/?id=10037" target="_blank" rel="noopener noreferrer">AI WEB 网页开发挑战赛<ArrowUpRight :size="12" /></a> 的 RAG、交互与可验证性要求完善。赛事规则与提交材料以官网为准。</footer>
  </div>
</template>
