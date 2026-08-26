import { readStoredToken } from "@/lib/auth/session";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8001";

/** FastAPI 侧用 HTTPBearer 校验同一个令牌，未登录时让请求带空头并由后端返回 401。 */
function authHeaders(): Record<string, string> {
  const token = readStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type Role = "user" | "assistant";

export interface EmotionData {
  [key: string]: unknown;
}

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  context?: string;
  emotion?: EmotionData | null;
}

/** 取值与后端 knowledge.py 的 DocumentInfo.source 一一对应 */
export type DocumentSource = "preloaded" | "uploaded" | "wiki";

export interface DocumentInfo {
  name: string;
  chars: number;
  indexed: boolean;
  source: DocumentSource;
}

export interface ChatDone {
  answer: string;
  intent: string;
  context: string;
  emotion: EmotionData | null;
}

export interface ChatStatus {
  stage: "intent" | "retrieved" | "generating";
  intent?: string;
  chars?: number;
}

export interface ChatHandlers {
  onStatus?: (status: ChatStatus) => void;
  onDelta?: (text: string) => void;
  onDone?: (result: ChatDone) => void;
  onError?: (message: string) => void;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...init?.headers },
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `请求失败（${response.status}）`);
  }
  return (await response.json()) as T;
}

export function createSession() {
  return request<{ thread_id: string }>("/api/sessions", { method: "POST" });
}

export function fetchMessages(threadId: string) {
  return request<Omit<ChatMessage, "id">[]>(
    `/api/sessions/${encodeURIComponent(threadId)}/messages`,
  );
}

export function fetchDocuments() {
  return request<DocumentInfo[]>("/api/documents");
}

export function uploadDocument(file: File) {
  const body = new FormData();
  body.append("file", file);
  return request<DocumentInfo>("/api/documents", { method: "POST", body });
}

export function clearDocuments() {
  return request<{ status: string }>("/api/documents", { method: "DELETE" });
}

/**
 * 对话走 SSE。EventSource 只能发 GET，这里用 fetch 手动解析事件帧。
 */
export async function streamChat(
  threadId: string,
  question: string,
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ thread_id: threadId, question }),
    signal,
  });

  if (!response.ok || !response.body) {
    handlers.onError?.(`连接后端失败（${response.status}）`);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE 以空行分隔事件帧
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      dispatchFrame(frame, handlers);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function dispatchFrame(frame: string, handlers: ChatHandlers) {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }

  if (dataLines.length === 0) return;

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return;
  }

  switch (event) {
    case "status":
      handlers.onStatus?.(payload as ChatStatus);
      break;
    case "delta":
      handlers.onDelta?.((payload as { text: string }).text);
      break;
    case "done":
      handlers.onDone?.(payload as ChatDone);
      break;
    case "error":
      handlers.onError?.((payload as { message: string }).message);
      break;
  }
}
