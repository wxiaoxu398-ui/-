import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { api, stream } from "../api";
import type { Document, Source, Message, Conversation, Health, Task } from "../types";

export const useWorkspace = defineStore("workspace", () => {
  const documents = ref<Document[]>([]),
    conversations = ref<Conversation[]>([]),
    messages = ref<Message[]>([]);
  const health = ref<Health | null>(null),
    task = ref<Task | null>(null),
    connected = ref(false),
    error = ref("");
  const activeId = ref<number | null>(null),
    selectedMessage = ref<number | null>(null),
    selectedSource = ref<number | null>(null);
  const selectedDocuments = ref<number[] | null>(null),
    generating = ref(false),
    loading = ref(false);
  const mode = computed(() =>
    health.value?.answer_mode === "deepseek" ? "DeepSeek 问答" : "原文摘录模式",
  );
  const sources = computed<Source[]>(
    () => messages.value.find((m) => m.id === selectedMessage.value)?.sources || [],
  );
  let controller: AbortController | null = null;
  async function refresh() {
    try {
      const [h, d, c] = await Promise.all([
        api<Health>("/health"),
        api<{ items: Document[]; task: Task }>("/documents"),
        api<{ items: Conversation[] }>("/conversations"),
      ]);
      health.value = h;
      documents.value = d.items;
      task.value = d.task;
      conversations.value = c.items;
      connected.value = true;
    } catch (e) {
      connected.value = false;
      throw e;
    }
  }
  async function initialize() {
    try {
      await refresh();
      const saved = Number(localStorage.getItem("medrag.conversation"));
      if (saved && conversations.value.some((c) => c.id === saved)) await load(saved);
    } catch (e) {
      error.value = (e as Error).message;
    }
  }
  function selectMessage(id: number) {
    selectedMessage.value = id;
    selectedSource.value = null;
  }
  function newChat() {
    if (generating.value) return;
    activeId.value = null;
    messages.value = [];
    selectedMessage.value = null;
    selectedSource.value = null;
    error.value = "";
    localStorage.removeItem("medrag.conversation");
  }
  async function load(id: number) {
    if (generating.value || loading.value) return;
    loading.value = true;
    try {
      const res = await api<{ items: Message[] }>(`/conversations/${id}/messages`);
      messages.value = res.items;
      activeId.value = id;
      selectMessage([...res.items].reverse().find((m) => m.role === "assistant")?.id || 0);
      localStorage.setItem("medrag.conversation", String(id));
      error.value = "";
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      loading.value = false;
    }
  }
  async function remove(id: number) {
    await api(`/conversations/${id}`, { method: "DELETE" });
    if (activeId.value === id) newChat();
    await refresh();
  }
  async function send(question: string) {
    if (generating.value || loading.value || !question.trim()) return;
    if (!connected.value) {
      error.value = "本地服务未连接，请双击启动脚本。";
      return;
    }
    generating.value = true;
    error.value = "";
    controller = new AbortController();
    const previous = [...messages.value];
    const reply: Message = {
      id: -Date.now(),
      role: "assistant",
      content: "",
      sources: [],
      status: "generating",
    };
    messages.value.push(
      { id: reply.id - 1, role: "user", content: question, sources: [], status: "complete" },
      reply,
    );
    selectMessage(reply.id);
    let hasMeta = false;
    try {
      await stream(
        "/chat/stream",
        {
          conversation_id: activeId.value,
          question,
          document_ids: selectedDocuments.value,
          top_k: 6,
        },
        controller.signal,
        (event, data) => {
          const current = messages.value[messages.value.length - 1];
          if (event === "meta") {
            hasMeta = true;
            activeId.value = data.conversation_id;
            current.id = data.message_id;
            selectedMessage.value = data.message_id;
            localStorage.setItem("medrag.conversation", String(data.conversation_id));
          }
          if (event === "sources") current.sources = data.sources;
          if (event === "delta") current.content += data.content;
          if (event === "done")
            current.status = data.finish_reason === "error" ? "error" : "complete";
          if (event === "error") {
            error.value = data.message;
            current.status = "error";
          }
        },
      );
    } catch (e) {
      if ((e as Error).name === "AbortError")
        messages.value[messages.value.length - 1].status = "stopped";
      else {
        error.value = (e as Error).message;
        if (!hasMeta) messages.value = previous;
        else messages.value[messages.value.length - 1].status = "error";
      }
    } finally {
      generating.value = false;
      controller = null;
      try {
        await refresh();
      } catch {
        /* Retain the answer and connection error on a server outage. */
      }
    }
  }
  function stop() {
    controller?.abort();
  }
  async function feedback(message: Message, rating: "up" | "down") {
    await api("/feedback", {
      method: "POST",
      body: JSON.stringify({ message_id: message.id, rating }),
    });
    message.feedback = rating;
  }
  async function ingest(rebuild = true) {
    await api("/documents/ingest", { method: "POST", body: JSON.stringify({ rebuild }) });
    await refresh();
  }
  return {
    documents,
    conversations,
    messages,
    health,
    task,
    connected,
    error,
    activeId,
    selectedMessage,
    selectedSource,
    selectedDocuments,
    generating,
    loading,
    mode,
    sources,
    refresh,
    initialize,
    selectMessage,
    newChat,
    load,
    remove,
    send,
    stop,
    feedback,
    ingest,
  };
});
