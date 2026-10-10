<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from "vue";
import { NModal, NConfigProvider } from "naive-ui";
import {
  BookOpen,
  BookMarked,
  Plus,
  MessageSquare,
  ArrowUp,
  ArrowUpRight,
  ChevronRight,
  ChevronDown,
  PanelRightClose,
  PanelRightOpen,
  ShieldCheck,
  Library,
  FileText,
  RefreshCw,
  Upload,
  Search,
  Settings,
  Trash2,
  X,
  Check,
  CircleHelp,
  Layers,
  Square,
  ClipboardCheck,
  ExternalLink,
  Sparkles,
  FolderOpen,
  AlertTriangle,
  Menu,
  LoaderCircle,
  Filter,
} from "lucide-vue-next";
import MessageItem from "./components/MessageItem.vue";
import EvaluationPanel from "./components/EvaluationPanel.vue";
import { useWorkspace } from "./stores/workspace";
import { api, openSource } from "./api";
import type { Document, Source, Message } from "./types";
const store = useWorkspace();
const view = ref<"chat" | "evaluation">("chat"),
  prompt = ref(""),
  search = ref(""),
  showSearch = ref(false);
const sourceOpen = ref(window.innerWidth > 1000),
  mobileLibrary = ref(false),
  input = ref<HTMLTextAreaElement>(),
  messagesEl = ref<HTMLElement>(),
  uploadInput = ref<HTMLInputElement>();
const settingsOpen = ref(false),
  helpOpen = ref(false),
  metadataOpen = ref(false),
  deleteTarget = ref<number | null>(null),
  saving = ref(false),
  uploading = ref(false),
  notice = ref("");
const editing = ref({
  id: 0,
  title: "",
  author: "",
  source: "",
  license: "",
  source_url: "",
});
const config = ref({
  api_key: "",
  clear_api_key: false,
  llm_model: "deepseek-flash",
  embedding_backend: "bge",
  similarity_threshold: 0.35,
  llm_configured: false,
});
const visibleDocs = computed(() =>
  store.documents.filter((d) =>
    (d.title + d.author).toLowerCase().includes(search.value.toLowerCase()),
  ),
);
const indexing = computed(() => store.task?.status === "processing");
const activeTitle = computed(
  () => store.conversations.find((c) => c.id === store.activeId)?.title || "新的学习会话",
);
const defaultSuggestions = [
  {
    icon: BookOpen,
    title: "理解一个概念",
    question: "心肌细胞动作电位分几期？各期离子机制是什么？",
    tag: "概念解析",
  },
  {
    icon: Layers,
    title: "梳理知识框架",
    question: "请根据教材梳理肾小球滤过的影响因素。",
    tag: "知识梳理",
  },
  {
    icon: BookMarked,
    title: "对照教材原文",
    question: "先天免疫与适应性免疫有哪些区别？请列出教材依据。",
    tag: "对比学习",
  },
];
const openRNLibrary = computed(() =>
  ["NBK595000", "NBK610815", "NBK596735"].every((id) =>
    store.documents.some((document) => document.source_url.includes(id)),
  ),
);
const chineseManuals = computed(() =>
  ["10665/372979", "10665/372970", "10665/372900"].every((id) =>
    store.documents.some((document) => document.status === "ready" && document.source_url.includes(id)),
  ),
);
const suggestions = computed(() =>
  chineseManuals.value
    ? [
        {
          ...defaultSuggestions[0],
          title: "理解生物安全",
          question: "教材如何定义生物安全？",
        },
        {
          ...defaultSuggestions[1],
          title: "梳理风险评估",
          question: "风险评估框架包括哪五个步骤？",
        },
        {
          ...defaultSuggestions[2],
          title: "理解防护装备",
          question: "紧密贴合型面罩通过什么方式形成保护屏障？",
        },
      ]
    : openRNLibrary.value
    ? [
        { ...defaultSuggestions[0], question: "药代动力学 pharmacokinetics 包含哪些阶段？" },
        {
          ...defaultSuggestions[1],
          title: "理解气体交换",
          question: "外呼吸 external respiration 时，气体在哪里交换？",
          tag: "基础生理",
        },
        {
          ...defaultSuggestions[2],
          question: "听到第一个 Korotkoff 声时记录的是哪种血压？",
        },
      ]
    : defaultSuggestions,
);
let timer: number | undefined, noticeTimer: number | undefined;
function notify(text: string) {
  notice.value = text;
  clearTimeout(noticeTimer);
  noticeTimer = window.setTimeout(() => (notice.value = ""), 4500);
}
function report(e: unknown) {
  store.error = e instanceof Error ? e.message : String(e);
}
onMounted(async () => {
  await store.initialize();
  timer = window.setInterval(async () => {
    try {
      await store.refresh();
    } catch {
      /* Keep offline indication; no repeating alerts. */
    }
  }, 3000);
});
onUnmounted(() => {
  clearInterval(timer);
  clearTimeout(noticeTimer);
  store.stop();
});
watch(
  () => store.messages.map((m) => m.content).join(""),
  async () => {
    await nextTick();
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight;
  },
);
function startChat() {
  view.value = "chat";
  store.newChat();
  mobileLibrary.value = false;
  nextTick(() => input.value?.focus());
}
function fill(question: string) {
  prompt.value = question;
  nextTick(() => input.value?.focus());
}
async function send() {
  const text = prompt.value.trim();
  if (!text || store.generating) return;
  prompt.value = "";
  await store.send(text);
}
function keydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
}
function filterDoc(id: number) {
  if (store.selectedDocuments === null) store.selectedDocuments = [id];
  else
    store.selectedDocuments = store.selectedDocuments.includes(id)
      ? store.selectedDocuments.filter((x) => x !== id)
      : [...store.selectedDocuments, id];
}
function editDoc(doc: Document) {
  editing.value = {
    id: doc.id,
    title: doc.title,
    author: doc.author,
    source: doc.source,
    license: doc.license,
    source_url: doc.source_url,
  };
  metadataOpen.value = true;
}
async function saveDoc() {
  saving.value = true;
  try {
    await api(`/documents/${editing.value.id}`, {
      method: "PATCH",
      body: JSON.stringify(editing.value),
    });
    await store.refresh();
    metadataOpen.value = false;
    notify("文档信息已保存");
  } catch (e) {
    report(e);
  } finally {
    saving.value = false;
  }
}
async function upload(event: Event) {
  const files = (event.target as HTMLInputElement).files;
  if (!files?.length) return;
  uploading.value = true;
  try {
    for (const file of files) {
      const body = new FormData();
      body.append("file", file);
      await api("/documents/upload", { method: "POST", body });
    }
    await store.refresh();
    notify("PDF 已导入，点击书名补充来源与许可证，再建立索引。");
  } catch (e) {
    report(e);
    await store.refresh().catch(() => {});
  } finally {
    uploading.value = false;
    if (uploadInput.value) uploadInput.value.value = "";
  }
}
async function ingest() {
  try {
    await store.ingest();
    notify("开始建立索引，首次使用需下载嵌入模型。");
  } catch (e) {
    report(e);
  }
}
async function settings() {
  try {
    const data = await api<typeof config.value>("/settings");
    config.value = { ...data, api_key: "", clear_api_key: false };
    settingsOpen.value = true;
  } catch (e) {
    report(e);
  }
}
async function saveSettings() {
  saving.value = true;
  const old = store.health?.embedding_backend;
  try {
    await api("/settings", {
      method: "POST",
      body: JSON.stringify(config.value),
    });
    config.value.api_key = "";
    await store.refresh();
    settingsOpen.value = false;
    notify(
      old === config.value.embedding_backend ? "设置已保存" : "检索模式已切换，请重新建立索引。",
    );
  } catch (e) {
    report(e);
  } finally {
    saving.value = false;
  }
}
async function remove() {
  if (deleteTarget.value === null) return;
  try {
    await store.remove(deleteTarget.value);
    deleteTarget.value = null;
  } catch (e) {
    report(e);
  }
}
function citation(message: Message, index: number) {
  store.selectMessage(message.id);
  store.selectedSource = index;
  sourceOpen.value = true;
}
async function open(source: Source) {
  try {
    await openSource(source);
  } catch (e) {
    report(e);
  }
}
async function feedback(message: Message, rating: "up" | "down") {
  try {
    await store.feedback(message, rating);
    notify("反馈已保存");
  } catch (e) {
    report(e);
  }
}
</script>
<template>
  <NConfigProvider
    :theme-overrides="{
      common: {
        primaryColor: '#245f55',
        borderRadius: '10px',
        fontFamily: 'Inter, Segoe UI, Microsoft YaHei, sans-serif',
      },
    }"
  >
    <div class="app-shell">
      <div class="disclaimer">
        <ShieldCheck :size="14" /><span>本工具仅供医学学习参考，不构成医疗建议</span
        ><span class="disclaimer-extra">不提供诊断、处方或治疗建议</span
        ><button aria-label="了解使用边界" @click="helpOpen = true">
          <CircleHelp :size="14" />
        </button>
      </div>
      <div class="workspace" :class="{ 'sources-hidden': !sourceOpen || view === 'evaluation' }">
        <aside class="sidebar" :class="{ 'mobile-visible': mobileLibrary }">
          <a href="#" class="brand" @click.prevent="startChat"
            ><span class="brand-mark"><BookOpen :size="23" /></span>
            <div>Med<span>RAG</span><small>医学教材智能问答助手</small></div></a
          >
          <button class="new-chat" :disabled="store.generating" @click="startChat">
            <Plus :size="17" />新建学习会话<span>＋</span>
          </button>
          <nav class="navigation">
            <button :class="{ active: view === 'chat' }" @click="view = 'chat'">
              <MessageSquare :size="17" />教材问答<ChevronRight :size="14" /></button
            ><button
              :class="{ active: view === 'evaluation' }"
              :disabled="store.generating"
              @click="view = 'evaluation'"
            >
              <ClipboardCheck :size="17" />质量评估<span class="beta-tag">20 题</span>
            </button>
          </nav>
          <section class="library-section">
            <div class="section-title">
              <span
                >文档库 <small>{{ store.documents.length }}</small></span
              >
              <div>
                <button
                  class="icon-button"
                  aria-label="搜索文档"
                  title="搜索文档"
                  @click="showSearch = !showSearch"
                >
                  <Search :size="14" /></button
                ><button
                  class="icon-button"
                  aria-label="导入 PDF"
                  title="导入 PDF"
                  :disabled="uploading || indexing"
                  @click="uploadInput?.click()"
                >
                  <Plus :size="16" />
                </button>
              </div>
            </div>
            <input
              v-if="showSearch"
              v-model="search"
              class="document-search"
              placeholder="搜索书名或作者"
              aria-label="搜索书名或作者"
            />
            <div class="library-scope">
              <button
                :class="{ active: store.selectedDocuments === null }"
                @click="store.selectedDocuments = null"
              >
                <Library :size="13" />全部教材</button
              ><span v-if="store.selectedDocuments !== null"
                >已选 {{ store.selectedDocuments.length }} 本</span
              ><span v-else>{{ store.health?.num_ready || 0 }} 本就绪</span>
            </div>
            <div class="document-list">
              <div v-for="doc in visibleDocs" :key="doc.id" class="document-row">
                <span class="document-icon"><FileText :size="18" /></span>
                <button class="document-info" @click="editDoc(doc)">
                  <strong :title="doc.title">{{ doc.title }}</strong
                  ><small
                    >{{ doc.num_pages }} 页 <span>·</span> {{ doc.author || "作者待补充" }}</small
                  ><span class="document-status" :class="doc.status"
                    ><i></i
                    >{{
                      {
                        ready: "已入库",
                        pending: "待入库",
                        processing: "入库中",
                        failed: "入库失败",
                      }[doc.status]
                    }}</span
                  >
                </button>
                <button
                  class="document-check"
                  :class="{
                    checked: store.selectedDocuments?.includes(doc.id),
                  }"
                  :aria-label="`只检索 ${doc.title}`"
                  :aria-pressed="store.selectedDocuments?.includes(doc.id) || false"
                  @click="filterDoc(doc.id)"
                >
                  <Check :size="12" />
                </button>
              </div>
              <div v-if="!store.documents.length" class="library-empty">
                <div class="empty-book"><FileText :size="23" /></div>
                <strong>从第一本教材开始</strong>
                <p>导入可自由使用的文字版 PDF，<br />让回答有据可查。</p>
                <button :disabled="uploading || !store.connected" @click="uploadInput?.click()">
                  <Upload :size="13" />{{ uploading ? "正在导入…" : "导入 PDF" }}
                </button>
              </div>
              <p v-else-if="!visibleDocs.length" class="muted small">没有找到匹配的文档。</p>
            </div>
            <input
              ref="uploadInput"
              type="file"
              accept="application/pdf,.pdf"
              multiple
              hidden
              @change="upload"
            />
            <button
              class="rebuild-button"
              :disabled="
                indexing || !store.connected || !store.documents.length || store.generating
              "
              @click="ingest"
            >
              <RefreshCw :size="14" :class="{ spinning: indexing }" />{{
                indexing
                  ? "正在建立索引…"
                  : store.health?.num_ready
                    ? "重建全部索引"
                    : "建立文档索引"
              }}<ArrowUpRight v-if="!indexing" :size="13" />
            </button>
            <div v-if="indexing" class="index-progress">
              <div
                :style="{
                  width: `${store.task?.total ? (store.task.processed / store.task.total) * 100 : 5}%`,
                }"
              ></div>
              <p>
                {{ store.task?.processed }}/{{ store.task?.total }} ·
                {{ store.task?.current || "正在准备" }}
              </p>
            </div>
            <p v-if="store.task?.error" class="index-error">
              {{ store.task.error }}
            </p>
          </section>
          <section class="history-section">
            <div class="section-title"><span>最近会话</span><MessageSquare :size="13" /></div>
            <div class="history-list">
              <div
                v-for="conv in store.conversations"
                :key="conv.id"
                class="history-row"
                :class="{ active: conv.id === store.activeId }"
              >
                <button
                  :disabled="store.generating"
                  @click="
                    view = 'chat';
                    store.load(conv.id);
                    mobileLibrary = false;
                  "
                >
                  <MessageSquare :size="13" /><span>{{ conv.title }}</span></button
                ><button
                  class="delete-history"
                  :disabled="store.generating"
                  :aria-label="`删除会话 ${conv.title}`"
                  @click="deleteTarget = conv.id"
                >
                  <Trash2 :size="13" />
                </button>
              </div>
              <div v-if="!store.conversations.length" class="history-empty">
                你的学习轨迹，会保存在这里。
              </div>
            </div>
          </section>
          <div class="sidebar-bottom">
            <div class="local-status">
              <i :class="{ offline: !store.connected }"></i
              >{{ store.connected ? "本地服务已连接" : "本地服务未连接" }}<span>LOCAL</span>
            </div>
            <div class="sidebar-tools">
              <button @click="settings"><Settings :size="15" />设置</button
              ><button @click="helpOpen = true"><CircleHelp :size="15" />使用说明</button>
            </div>
          </div>
        </aside>
        <main class="main-panel">
          <header class="main-header">
            <div>
              <button
                class="icon-button mobile-menu"
                aria-label="打开文档库"
                @click="mobileLibrary = !mobileLibrary"
              >
                <Menu :size="18" /></button
              ><span class="breadcrumb">学习工作台</span><ChevronRight :size="13" /><strong>{{
                view === "evaluation" ? "质量评估" : "教材问答"
              }}</strong>
            </div>
            <div class="header-controls">
              <span class="model-badge"
                ><i></i
                >{{ store.health?.llm_configured ? store.health.llm_model : "原文摘录" }}</span
              ><button
                v-if="view === 'chat'"
                class="icon-button"
                :aria-label="sourceOpen ? '收起来源面板' : '展开来源面板'"
                @click="sourceOpen = !sourceOpen"
              >
                <PanelRightClose v-if="sourceOpen" :size="18" /><PanelRightOpen v-else :size="18" />
              </button>
            </div>
          </header>
          <EvaluationPanel v-if="view === 'evaluation'" />
          <template v-else>
            <div
              ref="messagesEl"
              class="chat-scroll"
              :class="{ 'has-messages': store.messages.length }"
            >
              <div v-if="!store.messages.length" class="welcome">
                <div class="welcome-symbol">
                  <BookOpen :size="28" :stroke-width="1.6" /><span><Sparkles :size="12" /></span>
                </div>
                <div class="eyebrow">READ. ASK. UNDERSTAND.</div>
                <h1>从一个问题，读懂医学。</h1>
                <p class="welcome-copy">和你的教材对话。每一个回答，都能回到原文。</p>
                <div class="welcome-features">
                  <span><BookMarked :size="14" />原文可追溯</span><i></i
                  ><span><ShieldCheck :size="14" />资料不足则拒答</span><i></i
                  ><span><FolderOpen :size="14" />本地文档检索</span>
                </div>
                <div class="suggestion-heading">
                  <span>你可以从这里开始</span><span>试着提问 <ArrowUpRight :size="12" /></span>
                </div>
                <div class="suggestion-grid">
                  <button
                    v-for="item in suggestions"
                    :key="item.title"
                    class="suggestion"
                    @click="fill(item.question)"
                  >
                    <div><component :is="item.icon" :size="19" /><ArrowUpRight :size="14" /></div>
                    <strong>{{ item.title }}</strong>
                    <p>{{ item.question }}</p>
                    <small>{{ item.tag }}</small>
                  </button>
                </div>
                <div class="getting-started">
                  <span class="step-icon"><Upload :size="16" /></span>
                  <p>
                    <strong>{{
                      store.health?.num_ready
                        ? "教材已入库，试着问一个问题"
                        : store.documents.length
                          ? "教材准备好后，建立索引即可提问"
                          : "先导入教材，开启有依据的学习"
                    }}</strong
                    ><small>{{
                      chineseManuals
                        ? "中文学习手册已入库，可直接用中文提问。"
                        : openRNLibrary
                        ? "示例教材为英文，提问时可保留英文医学术语。"
                        : store.documents.length
                          ? "点击左侧书名，补充资料来源与许可证。"
                          : "支持文字版 PDF · 资料来源与许可证可逐本填写"
                    }}</small>
                  </p>
                  <button
                    @click="uploadInput?.click()"
                    :disabled="uploading || indexing || !store.connected"
                  >
                    导入教材<ChevronRight :size="14" />
                  </button>
                </div>
              </div>
              <div v-else class="messages">
                <div class="conversation-heading">
                  <span>{{ activeTitle }}</span
                  ><span>引用页码为 PDF 物理页码</span>
                </div>
                <MessageItem
                  v-for="message in store.messages"
                  :key="message.id"
                  :message="message"
                  :selected="store.selectedMessage === message.id"
                  :mode="store.mode"
                  @select="
                    store.selectMessage(message.id);
                    sourceOpen = true;
                  "
                  @citation="citation(message, $event)"
                  @feedback="feedback(message, $event)"
                  @error="store.error = $event"
                />
              </div>
            </div>
            <div class="composer-area">
              <div v-if="store.error" class="error-alert" role="alert">
                <AlertTriangle :size="15" /><span>{{ store.error }}</span
                ><button class="icon-button" aria-label="关闭错误提示" @click="store.error = ''">
                  <X :size="14" />
                </button>
              </div>
              <div class="composer" :class="{ busy: store.generating }">
                <textarea
                  ref="input"
                  v-model="prompt"
                  aria-label="输入医学学习问题"
                  placeholder="向教材提问，比如：心肌动作电位的离子机制是什么？"
                  rows="2"
                  maxlength="2000"
                  @keydown="keydown"
                ></textarea>
                <div class="composer-toolbar">
                  <div>
                    <span class="retrieval-label"
                      ><Library :size="13" />{{
                        store.selectedDocuments === null
                          ? "全部教材"
                          : `已选 ${store.selectedDocuments.length} 本`
                      }}<ChevronDown :size="12" /></span
                    ><span class="composer-mode">{{ store.mode }}</span>
                  </div>
                  <div>
                    <span class="keyboard-hint">Enter 发送 · Shift + Enter 换行</span
                    ><button
                      v-if="store.generating"
                      class="send-button stop-button"
                      aria-label="停止生成"
                      title="停止生成"
                      @click="store.stop"
                    >
                      <Square :size="16" /></button
                    ><button
                      v-else
                      class="send-button"
                      aria-label="发送问题"
                      title="发送问题"
                      :disabled="!prompt.trim() || !store.connected || indexing || store.loading"
                      @click="send"
                    >
                      <ArrowUp :size="19" />
                    </button>
                  </div>
                </div>
              </div>
              <div class="composer-note">
                <ShieldCheck :size="12" />{{
                  store.health?.llm_configured
                    ? "回答仅基于导入资料，请结合原文核对。"
                    : "未配置 API 密钥，仅返回原文摘录。可在设置中启用 DeepSeek 问答。"
                }}
              </div>
            </div>
          </template>
          <footer class="main-footer">
            <span
              >MedRAG <b>v{{ store.health?.version || "1.0.0" }}</b></span
            ><span>{{ store.health?.llm_model || "deepseek-flash" }} <i>·</i> 医学学习参考</span
            ><span>{{
              store.health?.embedding_backend === "lexical" ? "BM25 轻量检索" : "BGE + BM25"
            }}</span>
          </footer>
        </main>
        <aside v-if="sourceOpen && view === 'chat'" class="source-panel">
          <header>
            <div>
              <BookMarked :size="17" /><strong>参考来源</strong
              ><span class="count-badge">{{ store.sources.length }}</span>
            </div>
            <button class="icon-button" aria-label="收起来源" @click="sourceOpen = false">
              <PanelRightClose :size="17" />
            </button>
          </header>
          <div class="source-subtitle"><ShieldCheck :size="13" />每一条引用，回到原书原页。</div>
          <div v-if="!store.sources.length" class="source-empty">
            <div class="paper-illustration">
              <div class="paper-back"></div>
              <div class="paper-front">
                <BookMarked :size="22" /><i></i><i></i><i></i><i></i><span>[1]</span>
              </div>
              <span class="paper-check"><Check :size="13" /></span>
            </div>
            <strong>答案的依据，会在这里</strong>
            <p>提问后查看教材原文、引用页码，<br />点击编号即可定位来源。</p>
            <div class="source-example">
              <span>1</span>
              <div><i></i><i></i></div>
              <span>p. —</span>
            </div>
          </div>
          <div v-else class="source-list">
            <article
              v-for="source in store.sources"
              :key="source.index"
              class="source-card"
              :class="{ expanded: store.selectedSource === source.index }"
            >
              <button
                class="source-card-heading"
                @click="
                  store.selectedSource = store.selectedSource === source.index ? null : source.index
                "
              >
                <span class="citation-number">{{ source.index }}</span>
                <div>
                  <strong>{{ source.title }}</strong
                  ><small
                    >PDF 第 {{ source.page_start
                    }}{{
                      source.page_end !== source.page_start ? `–${source.page_end}` : ""
                    }}
                    页</small
                  >
                </div>
                <ChevronDown :size="14" />
              </button>
              <blockquote :class="{ clamped: store.selectedSource !== source.index }">
                {{ source.content }}
              </blockquote>
              <div class="source-metadata">
                <span>{{ source.source || "来源待补充" }}</span
                ><span>{{ source.license || "许可证待核实" }}</span>
              </div>
              <div class="source-card-footer">
                <span>相关度 {{ Math.round(source.score * 100) }}%</span
                ><button @click="open(source)">打开原文<ExternalLink :size="12" /></button>
              </div>
            </article>
          </div>
          <div class="source-bottom">
            <div><ShieldCheck :size="17" /><strong>有依据，才回答</strong></div>
            <p>资料不足时明确拒答。引用编号有效，<br />仍需核对原文是否支持对应结论。</p>
          </div>
        </aside>
      </div>
      <div v-if="notice" class="toast" role="status"><Check :size="15" />{{ notice }}</div>
      <NModal v-model:show="settingsOpen"
        ><section class="modal-card">
          <div class="modal-heading">
            <h2>设置</h2>
            <button class="icon-button" aria-label="关闭设置" @click="settingsOpen = false">
              <X :size="19" />
            </button>
          </div>
          <p class="muted">密钥仅保存到本机 backend/.env，回答时发送到 DeepSeek API。</p>
          <label
            >DeepSeek API 密钥
            <span>{{ config.llm_configured ? "已配置；留空保留原密钥" : "尚未配置" }}</span
            ><input
              v-model="config.api_key"
              type="password"
              autocomplete="off"
              placeholder="sk-…" /></label
          ><label v-if="config.llm_configured" class="checkbox-label"
            ><input v-model="config.clear_api_key" type="checkbox" />清除已保存的密钥</label
          ><label>模型名称<input v-model="config.llm_model" placeholder="deepseek-flash" /></label
          ><label
            >本地检索方式<select v-model="config.embedding_backend">
              <option value="bge">BGE 向量 + BM25 混合检索</option>
              <option value="lexical">BM25 轻量模式（无需下载模型）</option>
            </select></label
          ><label
            >向量相似度阈值 <span>{{ config.similarity_threshold }}</span
            ><input
              v-model.number="config.similarity_threshold"
              type="range"
              min="0.1"
              max="0.9"
              step="0.05"
          /></label>
          <p class="modal-note">
            切换检索方式后需重建索引。阈值需结合实际教材评估，相关度不代表答案准确率。
          </p>
          <button
            class="primary-button modal-save"
            :disabled="saving || !config.llm_model.trim()"
            @click="saveSettings"
          >
            {{ saving ? "保存中…" : "保存设置" }}
          </button>
        </section></NModal
      >
      <NModal v-model:show="metadataOpen"
        ><section class="modal-card">
          <div class="modal-heading">
            <h2>教材信息</h2>
            <button class="icon-button" aria-label="关闭教材信息" @click="metadataOpen = false">
              <X :size="19" />
            </button>
          </div>
          <label>书名<input v-model="editing.title" /></label
          ><label>作者<input v-model="editing.author" placeholder="作者或出版机构" /></label
          ><label>资料来源<input v-model="editing.source" placeholder="例如 OpenStax" /></label
          ><label
            >许可证<input
              v-model="editing.license"
              placeholder="按原书填写，例如 CC BY 4.0" /></label
          ><label
            >来源链接<input v-model="editing.source_url" type="url" placeholder="https://…"
          /></label>
          <p class="modal-note">
            请按每本资料的实际许可填写。NCBI Bookshelf、WHO 或 PMC 上的内容并不都适用同一许可证。
          </p>
          <p v-if="store.documents.find((d) => d.id === editing.id)?.error" class="error-alert">
            {{ store.documents.find((d) => d.id === editing.id)?.error }}
          </p>
          <button
            class="primary-button modal-save"
            :disabled="saving || !editing.title.trim()"
            @click="saveDoc"
          >
            保存信息
          </button>
        </section></NModal
      >
      <NModal v-model:show="helpOpen"
        ><section class="modal-card help-card">
          <div class="modal-heading">
            <h2>开始使用 MedRAG</h2>
            <button class="icon-button" aria-label="关闭使用说明" @click="helpOpen = false">
              <X :size="19" />
            </button>
          </div>
          <ol>
            <li>
              <strong>导入文字版 PDF</strong>
              <p>点击文档库的 ＋，或将 PDF 放入 backend/app/data/books/。扫描件需先进行 OCR。</p>
            </li>
            <li>
              <strong>补充来源，建立索引</strong>
              <p>点击书名填写作者、来源与许可证，再点“建立文档索引”。首次 BGE 入库会下载模型。</p>
            </li>
            <li>
              <strong>配置密钥，开始提问</strong>
              <p>在设置中填写 DeepSeek API 密钥。未配置时只展示原文摘录。</p>
            </li>
            <li>
              <strong>核对引用</strong>
              <p>
                点击回答中的编号，在来源面板查看片段并打开 PDF 原页。PDF
                物理页码可能与书内印刷页码不同。
              </p>
            </li>
          </ol>
          <div class="help-boundary">
            <ShieldCheck :size="19" />
            <p>
              本工具仅供医学学习，不接收患者资料，不提供诊断、处方或治疗建议。请只导入可合法使用的资料。
            </p>
          </div>
        </section></NModal
      >
      <NModal
        :show="deleteTarget !== null"
        @update:show="
          (value) => {
            if (!value) deleteTarget = null;
          }
        "
        ><section class="modal-card delete-modal">
          <h2>删除这个会话？</h2>
          <p class="muted">该会话的消息与反馈会一并删除。</p>
          <div>
            <button class="secondary-button" @click="deleteTarget = null">保留会话</button
            ><button class="danger-button" @click="remove">删除会话</button>
          </div>
        </section></NModal
      >
    </div>
  </NConfigProvider>
</template>
