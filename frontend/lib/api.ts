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
  /** 这个工具最近一般要多久（毫秒），样本够了才有；进度条按它估节奏 */
  eta_ms?: number;
}

export type JwMode = "off" | "demo" | "zhengfang";

export interface JwAccount {
  mode: JwMode;
  bound: boolean;
  /** 打了码的学号 */
  username: string;
  boundAt: number | null;
}

/** 与后端 today_card.build_card 一一对应；时间都是 "08:00"，教务没给节次时间时为空串 */
export interface TodayLesson {
  course: string;
  periods: string;
  start: string;
  end: string;
  room: string;
  teacher: string;
}

export interface TodayExam {
  course: string;
  name: string;
  /** YYYY-MM-DD */
  date: string;
  start: string;
  end: string;
  room: string;
  seat: string;
  days_left: number;
}

/** enabled=false 学校没开教务；bound=false 学生还没绑定，后面的字段都没有 */
export interface TodayCardData {
  enabled: boolean;
  bound: boolean;
  date?: string;
  weekday?: string;
  week?: number | null;
  week_unknown?: boolean;
  lessons?: TodayLesson[];
  exams?: TodayExam[];
}

/** 与后端 comfort.safety_card 一一对应 */
export interface SafetyCardData {
  level: "crisis";
  hotlines: { name: string; phone: string }[];
  school: string;
}

/** 与后端 agent.ToolContext.table 一一对应：空列已被后端去掉，单元格都是字符串 */
export interface ChatTable {
  title: string;
  columns: { key: string; label: string }[];
  rows: Record<string, string>[];
  /** 工具查到的总行数；比 rows 多说明后端截断了 */
  total: number;
}

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  context?: string;
  sources?: ChatSource[];
  tables?: ChatTable[];
  emotion?: EmotionData | null;
  safety?: SafetyCardData | null;
  /** 这一轮查过的工具，只在前端内存里，刷新后没有 */
  tools?: ChatToolEvent[];
  /** 助手回答在库里的编号，有它才能点赞点踩；访客和没入库的没有 */
  serverId?: string;
  /** 自己打的分：1 赞、-1 踩、0 没打 */
  feedback?: FeedbackRating;
  feedbackReason?: FeedbackReason;
  /** 查完教务后建议接着问的话，只在最新一条回答下面显示，刷新后没有 */
  followUps?: string[];
  /** 学生附的图（data URL），只在前端内存里给自己看和重新生成用；后端不存，刷新后没有 */
  images?: string[];
}

export type FeedbackRating = 1 | -1 | 0;
export type FeedbackReason = "" | "wrong" | "outdated" | "verbose" | "other";

/** 后端 api.Message：比 ChatMessage 多一个库里的编号，没有前端自己的 id */
export type StoredMessage = Omit<ChatMessage, "id" | "serverId" | "feedbackReason"> & {
  id: string;
  feedback_reason: FeedbackReason;
};

/** 会话列表的一行，与后端 api.SessionSummary 一一对应 */
export interface SessionSummary {
  thread_id: string;
  title: string;
  /** ISO 时间 */
  updated_at: string;
}

/** 会话已删除或不属于当前账号时，后端统一回 404 */
export class SessionGoneError extends Error {}

export interface MemoryNote {
  id: string;
  content: string;
  created_at: number;
  origin: "manual" | "auto";
  /** profile 身份档案 / preference 长期偏好 / ongoing 在办的事 / fact 其他 */
  kind: "profile" | "preference" | "ongoing" | "fact";
  /** grade / college / major；同一槽位只保留最新说法 */
  slot: "" | "grade" | "college" | "major";
  pinned: boolean;
  /** 用户自己要求记下的隐私内容：只在记忆页显示，不会自动进提示词 */
  sensitive: boolean;
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
  tables: ChatTable[];
  follow_ups?: string[];
  emotion: EmotionData | null;
  safety: SafetyCardData | null;
  /** 这一轮入库后那条回答的编号；没入库（访客、库连不上）时没有 */
  message_id?: string;
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
    throw new ApiError(response.status, detail || `请求失败（${response.status}）`);
  }
  if (response.status === NO_CONTENT) return undefined as T;
  return (await response.json()) as T;
}

const NO_CONTENT = 204;
const NOT_FOUND = 404;

/** message 仍是后端原始报错体，errorDetail() 照旧能取出那句话 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function createSession() {
  return request<{ thread_id: string }>("/api/sessions", { method: "POST" });
}

export async function fetchMessages(threadId: string) {
  try {
    return await request<StoredMessage[]>(`/api/sessions/${encodeURIComponent(threadId)}/messages`);
  } catch (err) {
    if (err instanceof ApiError && err.status === NOT_FOUND) throw new SessionGoneError(errorDetail(err));
    throw err;
  }
}

export function listSessions() {
  return request<SessionSummary[]>("/api/sessions");
}

export function deleteSession(threadId: string) {
  return request<void>(`/api/sessions/${encodeURIComponent(threadId)}`, { method: "DELETE" });
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
  return request<{ assistant_name: string; query_only: boolean; vision?: boolean }>("/api/workspace");
}

export type AiServiceName = "chat" | "reader" | "vision" | "embedding" | "map" | "search" | "jw" | "news";

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

/** day 是学生本机的日期（YYYY-MM-DD），免得服务器在别的时区时凌晨看到昨天的课 */
export function fetchTodayCard(day: string, signal?: AbortSignal) {
  return request<TodayCardData>(`/api/today?day=${encodeURIComponent(day)}`, { signal });
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

/** 给自己会话里的一条回答点赞 / 点踩；rating 为 0 是取消。reason 只在点踩时有意义 */
export function rateMessage(messageId: string, rating: FeedbackRating, reason: FeedbackReason = "") {
  return request<void>("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: messageId, rating, reason }),
  });
}

export type UsageDays = 7 | 30 | 90;

export interface UsageTotals {
  questions: number;
  guest: number;
  comfort: number;
  refused: number;
  no_source: number;
  /** 答上的占比（不含情绪倾诉）；这段时间没人问就是 null */
  answer_rate: number | null;
  up: number;
  down: number;
  tool_calls: number;
  tool_failures: number;
}

export interface UsageTopQuestion {
  question: string;
  count: number;
  last_at: string;
}

export interface UsageGap {
  question: string;
  count: number;
  last_at: string;
  /** refused 拒答 / no_source 没检索到资料 / downvoted 被点踩，各出现几次 */
  kinds: Partial<Record<"refused" | "no_source" | "downvoted", number>>;
  reasons: Partial<Record<Exclude<FeedbackReason, "">, number>>;
  /** 被点踩的那条回答的开头，让管理员知道答了什么 */
  answer_hint: string;
}

export interface UsageTool {
  name: string;
  label: string;
  calls: number;
  failures: number;
  failure_rate: number;
  avg_ms: number;
  last_error: string;
}

export interface UsageReport {
  days: number;
  totals: UsageTotals;
  top_questions: UsageTopQuestion[];
  unanswered: UsageGap[];
  tools: UsageTool[];
}

export function fetchUsage(days: UsageDays, signal?: AbortSignal) {
  return request<UsageReport>(`/api/admin/usage?days=${days}`, { signal });
}

/** 课前几分钟提醒；0 = 不提醒。与后端 calendar_ics.ALARM_CHOICES 一致 */
export const CALENDAR_ALARMS = [0, 5, 10, 15, 30, 60] as const;
export type CalendarAlarm = (typeof CALENDAR_ALARMS)[number];

export interface CalendarSubscription {
  active: boolean;
  alarm?: CalendarAlarm;
  created_at?: number;
}

/** 刚生成的订阅：令牌只在这一次返回 */
export interface NewCalendarSubscription {
  token: string;
  path: string;
  alarm: CalendarAlarm;
}

/** 课表和考试导出成 .ics 文件并交给浏览器下载。需要带登录信息，所以不能用普通链接。 */
export async function downloadCalendar(alarm: CalendarAlarm, termStart?: string): Promise<void> {
  const query = new URLSearchParams({ alarm: String(alarm) });
  if (termStart) query.set("term_start", termStart);
  const response = await fetch(`${API_BASE}/api/calendar/export?${query}`, { headers: authHeaders() });
  if (!response.ok) {
    throw new ApiError(response.status, (await response.text().catch(() => "")) || `请求失败（${response.status}）`);
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = "我的课表.ics";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function fetchCalendarSubscription() {
  return request<CalendarSubscription>("/api/calendar/subscription");
}

export function createCalendarSubscription(alarm: CalendarAlarm) {
  return request<NewCalendarSubscription>("/api/calendar/subscription", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ alarm }),
  });
}

export function deleteCalendarSubscription() {
  return request<void>("/api/calendar/subscription", { method: "DELETE" });
}

/** 填进手机日历的订阅地址：日历软件直接访问后端，所以用后端的地址 */
export function calendarFeedUrl(path: string): string {
  return `${API_BASE.replace(/\/+$/, "")}${path}`;
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

export function updateMemory(id: string, patch: { content?: string; pinned?: boolean }) {
  return request<MemoryNote>(`/api/memories/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export function clearMemories() {
  return request<{ status: string; removed: number }>("/api/memories", { method: "DELETE" });
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
  /** 重新生成 / 改了再问：被替换掉的最后一轮的原提问 */
  replaceQuestion?: string,
  /** 附图（data URL），后端读完变成文字就丢，不入库 */
  images?: string[],
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ thread_id: threadId, question, llm, replace_question: replaceQuestion, images }),
    signal,
  });

  if (response.status === NOT_FOUND) throw new SessionGoneError(await response.text().catch(() => ""));
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
