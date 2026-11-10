import type { Message } from "../types";

export function conversationMarkdown(title: string, messages: Message[]): string {
  return [
    "# " + title,
    "本工具仅供医学学习参考，不构成医疗建议。引用页码均为 PDF 物理页码。",
    ...messages.map(message => [
      message.role === "user" ? "## 问题" : "## 教材回答",
      message.content || "（未生成内容）",
      message.status !== "complete" ? "状态：" + message.status + "，内容可能不完整。" : "",
      ...message.sources.map(source => "- [" + source.index + "] 《" + source.title + "》· PDF 第 "
        + source.page_start + (source.page_end !== source.page_start ? "–" + source.page_end : "")
        + " 页；来源：" + (source.source || "待补充") + "；许可：" + (source.license || "待补充")
        + "；文件 SHA-256：" + source.file_hash),
    ].filter(Boolean).join("\n\n")),
  ].join("\n\n") + "\n";
}

export function downloadText(filename: string, content: string, type = "text/markdown;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
