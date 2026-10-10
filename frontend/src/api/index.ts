export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch("/api" + path, {
    ...init,
    headers: {
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init.headers,
    },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error?.message || `请求失败 (${res.status})`);
  }
  return res.json();
}
export async function stream(
  path: string,
  payload: unknown,
  signal: AbortSignal,
  onEvent: (event: string, data: any) => void,
) {
  const res = await fetch("/api" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error?.message || "连接失败");
  }
  if (!res.body) throw new Error("浏览器未提供响应流");
  const reader = res.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "",
    completed = false;
  function parse(part: string) {
    const lines = part.split("\n"),
      name = lines
        .find((l) => l.startsWith("event:"))
        ?.slice(6)
        .trim();
    const data = lines
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trimStart())
      .join("\n");
    if (name && data) {
      if (name === "done") completed = true;
      onEvent(name, JSON.parse(data));
    }
  }
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      parts.forEach(parse);
      if (done) {
        if (buffer.trim()) parse(buffer);
        break;
      }
    }
    if (!completed) throw new Error("连接中断，回答可能未完成。");
  } finally {
    reader.releaseLock();
  }
}
export async function openSource(source: {
  document_id: number;
  file_hash: string;
  page_start: number;
}) {
  const popup = window.open("", "_blank");
  if (popup) popup.opener = null;
  const url = `/api/documents/${source.document_id}/file?hash=${encodeURIComponent(source.file_hash)}`;
  // Check first so an unavailable/replaced original produces a useful in-app error.
  try {
    const res = await fetch(url, { method: "HEAD" });
    if (!res.ok && res.status !== 405)
      throw new Error("原始 PDF 不可用或已替换，历史引用页码无法核验。");
    if (res.status === 405) {
      const check = await fetch(url, { headers: { Range: "bytes=0-0" } });
      if (!check.ok) {
        const data = await check.json().catch(() => ({}));
        throw new Error(data.error?.message || "无法打开 PDF");
      }
      await check.body?.cancel();
    }
    if (popup) popup.location.href = url + `#page=${source.page_start}`;
    else throw new Error("请允许本页面打开新窗口后重试。");
  } catch (e) {
    popup?.close();
    throw e;
  }
}
