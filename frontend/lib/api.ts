import { readStoredToken } from "@/lib/auth/session";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8001";

/** 嵌入挂件页面用访客令牌，不读登录态；挂件跑在自己的 iframe 文档里，不会串到主站 */
let tokenOverride: string | null = null;

export function setAccessTokenOverride(token: string | null): void {
  tokenOverride = token;
}

/** FastAPI 侧用 HTTPBearer 校验同一个令牌，未登录时让请求带空头并由后端返回 401。 */
function authHeaders(): Record<string, string> {
  const token = tokenOverride ?? readStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type Role = "user" | "assistant";

export interface EmotionData {
  [key: string]: unknown;
}

/** 与后端 graph_app._chunk_sources 的结构一一对应；Kimi 精读路径没有片段与分数 */
export interface ChatSource {
  index: number;
  source: string;
  snippet: string;
  score: number | null;
  rerank_score: number | null;
  /** 工具问答的来源（学校通知、地图导航）带链接 */
  url?: string;
}

/** 与后端 agent.run 推送的工具进度一一对应 */
export interface ChatToolEvent {
  name: string;
  label: string;
  state: "running" | "done";
  ok?: boolean;
}

export type JwMode = "off" | "demo" | "zhengfang";

export interface JwAccount {
  mode: JwMode;
  bound: boolean;
  /** 打了码的学号 */
  username: string;
  boundAt: number | null;
}

/** 与后端 comfort.safety_card 一一对应 */
export interface SafetyCardData {
  level: "crisis";
  hotlines: { name: string; phone: string }[];
  school: string;
}

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  context?: string;
  sources?: ChatSource[];
  emotion?: EmotionData | null;
  safety?: SafetyCardData | null;
}

export interface MemoryNote {
  id: string;
  content: string;
  created_at: number;
  origin: "manual" | "auto";
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
  sources: ChatSource[];
  emotion: EmotionData | null;
  safety: SafetyCardData | null;
}

export interface ChatStatus {
  stage: "intent" | "retrieved" | "generating" | "remembered";
  intent?: string;
  chars?: number;
  sources?: number;
  content?: string;
}

export interface ChatHandlers {
  onStatus?: (status: ChatStatus) => void;
  onTool?: (event: ChatToolEvent) => void;
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

export function fetchWorkspace() {
  return request<{ assistant_name: string; query_only: boolean }>("/api/workspace");
}

export type AiServiceName = "chat" | "reader" | "embedding" | "map" | "search" | "jw" | "news";

export interface AiServiceStatus {
  configured: boolean;
  /** 学校自配 / 服务器 .env 的平台默认；没配时为 null */
  source: "school" | "platform" | null;
  model: string;
}

export type AiStatus = Record<AiServiceName, AiServiceStatus>;

export function fetchAiStatus() {
  return request<AiStatus>("/api/ai/status");
}

/** 管理员：用本校当前生效的配置真实调一次 */
export function testAiService(service: AiServiceName) {
  return request<{ ok: boolean; source: string; model: string }>("/api/ai/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service }),
  });
}

export function fetchJwAccount() {
  return request<JwAccount>("/api/jw/account");
}

/** 需要验证码时返回 { captcha: 图片 base64 }，填好后连同账号密码再提交一次 */
export function bindJwAccount(input: { username: string; password: string; captcha?: string }) {
  return request<JwAccount | { captcha: string }>("/api/jw/account", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function unbindJwAccount() {
  return request<JwAccount>("/api/jw/account", { method: "DELETE" });
}

/** 取值与后端 jw_account.wecom_poll 一一对应 */
export type WecomStatus = "waiting" | "scanned" | "cancelled" | "expired" | "bound";

/** 企业微信扫码绑定：开一张二维码（PNG base64），id 用来轮询这一张 */
export function startJwWecom() {
  return request<{ id: string; qr: string; expiresIn: number }>("/api/jw/account/wecom", { method: "POST" });
}

/** 长轮询这张二维码的一轮扫码结果，最多挂 30 秒 */
export function pollJwWecom(id: string, signal?: AbortSignal) {
  return request<{ status: WecomStatus; account?: JwAccount }>(`/api/jw/account/wecom?id=${encodeURIComponent(id)}`, { signal });
}

/** FastAPI 的 HTTPException 报错体是 {"detail": "..."}，取出那句话 */
export function errorDetail(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  try {
    const detail = (JSON.parse(message) as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : message;
  } catch {
    return message;
  }
}

export function importDocumentUrl(url: string) {
  return request<DocumentInfo>("/api/documents/url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
}

export function fetchMemories() {
  return request<MemoryNote[]>("/api/memories");
}

export function addMemories(contents: string[]) {
  return request<MemoryNote[]>("/api/memories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contents }),
  });
}

export function deleteMemory(id: string) {
  return request<{ status: string }>(`/api/memories/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/**
 * 对话走 SSE。EventSource 只能发 GET，这里用 fetch 手动解析事件帧。
 */
export interface LlmChatConfig {
  api_key: string;
  base_url: string;
  model: string;
  stream: boolean;
}

export async function pingLlm(settings: {
  apiKey: string;
  baseUrl: string;
  model: string;
}): Promise<{ ok: boolean; models: string[] }> {
  const response = await fetch(`${API_BASE}/api/llm/ping`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      api_key: settings.apiKey,
      base_url: settings.baseUrl,
      model: settings.model,
    }),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `连通失败（${response.status}）`);
  }
  return (await response.json()) as { ok: boolean; models: string[] };
}

export async function streamChat(
  threadId: string,
  question: string,
  handlers: ChatHandlers,
  signal?: AbortSignal,
  llm?: LlmChatConfig,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ thread_id: threadId, question, llm }),
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
    case "tool":
      handlers.onTool?.(payload as ChatToolEvent);
      break;
    case "done":
      handlers.onDone?.(payload as ChatDone);
      break;
    case "error":
      handlers.onError?.((payload as { message: string }).message);
      break;
  }
}
