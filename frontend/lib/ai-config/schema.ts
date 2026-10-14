/** 学校 AI 服务配置的请求体校验与对外形状。密钥只进不出：读回时只给末四位。 */
import type { ValidationResult } from "@/lib/auth/schema";

export const AI_SERVICES = ["chat", "reader"] as const;
export type AiService = (typeof AI_SERVICES)[number];

export const BASE_URL_MAX = 200;
export const MODEL_MAX = 80;
export const API_KEY_MAX = 300;

export interface AiEndpointInput {
  baseUrl: string;
  model: string;
  /** 空串表示不改原来那把 */
  apiKey: string;
  clearKey: boolean;
}

/** 只有一把 Key、没有网址和模型可选的服务：高德地图、博查搜索 */
export const KEY_SERVICES = ["map", "search"] as const;
export type KeyService = (typeof KEY_SERVICES)[number];

export interface KeyInput {
  apiKey: string;
  clearKey: boolean;
}

export const JW_MODES = ["off", "demo", "zhengfang"] as const;
export type JwMode = (typeof JW_MODES)[number];

export interface JwConfig {
  mode: JwMode;
  /** 统一身份认证（CAS）根地址；正方教务模式必填 */
  casUrl: string;
  /** 正方教务根地址（…/jwglxt）；正方教务模式必填 */
  baseUrl: string;
}

export const NEWS_URLS_MAX = 10;

export const EMBEDDING_PROVIDERS = ["", "local", "zhipu", "openai"] as const;
export type EmbeddingProvider = (typeof EMBEDDING_PROVIDERS)[number];

/** 与 backend/ai_config.py 的 RESERVED_LOCAL_EMBEDDING_MODELS 保持一致。只展示，不加载。 */
export const LOCAL_EMBEDDING_MODELS = [
  { id: "BAAI/bge-small-zh-v1.5", label: "bge-small-zh" },
  { id: "BAAI/bge-base-zh-v1.5", label: "bge-base-zh" },
  { id: "BAAI/bge-m3", label: "bge-m3" },
] as const;

export interface EmbeddingInput {
  /** 空串或 zhipu 都走智谱；local / openai 只预留，后端不加载 */
  provider: EmbeddingProvider;
  model: string;
  baseUrl: string;
  apiKey: string;
  clearKey: boolean;
}

export interface AiConfigInput extends Record<AiService, AiEndpointInput>, Record<KeyService, KeyInput> {
  jw: JwConfig;
  newsUrls: string[];
  embedding: EmbeddingInput;
}

export interface AiEndpointView {
  baseUrl: string;
  model: string;
  hasKey: boolean;
  keyHint: string;
}

export interface KeyView {
  hasKey: boolean;
  keyHint: string;
}

export interface EmbeddingView {
  provider: EmbeddingProvider;
  model: string;
  baseUrl: string;
  hasKey: boolean;
  keyHint: string;
}

export interface AiConfigView extends Record<AiService, AiEndpointView>, Record<KeyService, KeyView> {
  jw: JwConfig;
  newsUrls: string[];
  embedding: EmbeddingView;
}

const SERVICE_NAME: Record<AiService | KeyService, string> = {
  chat: "对话模型",
  reader: "长文档精读",
  map: "高德地图 Key",
  search: "博查搜索 Key",
};

function isPrivateHost(hostname: string): boolean {
  const host = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".internal")) return true;
  if (host.includes(":")) return true;
  const parts = host.split(".").map(Number);
  if (parts.length !== 4 || parts.some((n) => !Number.isInteger(n))) return false;
  const [a, b] = parts;
  return (
    a === 10 ||
    a === 127 ||
    a === 0 ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168)
  );
}

/**
 * 服务端会拿学校填的网址发请求：只收 https，且拒绝本机与内网地址，免得被当成探测内网的跳板。
 * 空串表示用默认网址。
 */
export function normalizeBaseUrl(raw: string): string | null {
  const trimmed = raw.trim().replace(/\/+$/, "");
  if (!trimmed) return "";
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.username || url.password || isPrivateHost(url.hostname)) return null;
  return trimmed;
}

function parseEndpoint(service: AiService, raw: unknown): ValidationResult<AiEndpointInput> {
  const source = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const name = SERVICE_NAME[service];
  const baseUrl = normalizeBaseUrl(typeof source.baseUrl === "string" ? source.baseUrl : "");
  const model = typeof source.model === "string" ? source.model.trim() : "";
  const apiKey = typeof source.apiKey === "string" ? source.apiKey.trim() : "";

  if (baseUrl === null || baseUrl.length > BASE_URL_MAX) {
    return { ok: false, message: `${name}的接口网址需要是 https 公网地址` };
  }
  if (model.length > MODEL_MAX || /\s/.test(model)) {
    return { ok: false, message: `${name}的模型 ID 不能含空格，且不超过 ${MODEL_MAX} 字符` };
  }
  if (apiKey.length > API_KEY_MAX || /\s/.test(apiKey)) {
    return { ok: false, message: `${name}的密钥不能含空格，且不超过 ${API_KEY_MAX} 字符` };
  }
  return { ok: true, value: { baseUrl, model, apiKey, clearKey: source.clearKey === true } };
}

function parseKey(service: KeyService, raw: unknown): ValidationResult<KeyInput> {
  const source = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const apiKey = typeof source.apiKey === "string" ? source.apiKey.trim() : "";
  if (apiKey.length > API_KEY_MAX || /\s/.test(apiKey)) {
    return { ok: false, message: `${SERVICE_NAME[service]}不能含空格，且不超过 ${API_KEY_MAX} 字符` };
  }
  return { ok: true, value: { apiKey, clearKey: source.clearKey === true } };
}

/** 后端会拿这两个地址替学生登录、查教务，同样只收 https 公网地址 */
function parseJw(raw: unknown): ValidationResult<JwConfig> {
  const source = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const mode = (JW_MODES as readonly unknown[]).includes(source.mode) ? (source.mode as JwMode) : "off";
  const casUrl = normalizeBaseUrl(typeof source.casUrl === "string" ? source.casUrl : "");
  const baseUrl = normalizeBaseUrl(typeof source.baseUrl === "string" ? source.baseUrl : "");
  if (casUrl === null || casUrl.length > BASE_URL_MAX) return { ok: false, message: "统一身份认证地址需要是 https 公网地址" };
  if (baseUrl === null || baseUrl.length > BASE_URL_MAX) return { ok: false, message: "教务系统地址需要是 https 公网地址" };
  if (mode === "zhengfang" && (!casUrl || !baseUrl)) {
    return { ok: false, message: "对接正方教务需要填统一身份认证地址和教务系统地址" };
  }
  return { ok: true, value: { mode, casUrl, baseUrl } };
}

function parseNewsUrls(raw: unknown): ValidationResult<string[]> {
  const lines = Array.isArray(raw) ? raw : typeof raw === "string" ? raw.split("\n") : [];
  const urls: string[] = [];
  for (const line of lines) {
    if (typeof line !== "string" || !line.trim()) continue;
    const url = normalizePublicUrl(line);
    if (url === null || url.length > BASE_URL_MAX) return { ok: false, message: `通知来源「${line.trim()}」需要是公网网址` };
    if (!urls.includes(url)) urls.push(url);
  }
  if (urls.length > NEWS_URLS_MAX) return { ok: false, message: `通知来源最多 ${NEWS_URLS_MAX} 个` };
  return { ok: true, value: urls };
}

/** 学校官网不少还是 http；通知列表页只读不传密钥，放行 http，但同样拒绝内网 */
function normalizePublicUrl(raw: string): string | null {
  const trimmed = raw.trim();
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || isPrivateHost(url.hostname)) return null;
  return trimmed;
}

function parseEmbedding(raw: unknown): ValidationResult<EmbeddingInput> {
  const source = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const provider = (EMBEDDING_PROVIDERS as readonly unknown[]).includes(source.provider)
    ? (source.provider as EmbeddingProvider)
    : "";
  const model = typeof source.model === "string" ? source.model.trim() : "";
  const apiKey = typeof source.apiKey === "string" ? source.apiKey.trim() : "";
  const baseUrl = normalizeBaseUrl(typeof source.baseUrl === "string" ? source.baseUrl : "");

  if (model.length > MODEL_MAX || /\s/.test(model)) {
    return { ok: false, message: `向量模型 ID 不能含空格，且不超过 ${MODEL_MAX} 字符` };
  }
  if (apiKey.length > API_KEY_MAX || /\s/.test(apiKey)) {
    return { ok: false, message: "向量化密钥不能含空格，且不超过 300 字符" };
  }
  if (provider === "zhipu" && model && model !== "embedding-3") {
    return { ok: false, message: "智谱向量化目前只用 embedding-3" };
  }
  let embeddingBaseUrl = "";
  if (provider === "openai") {
    if (baseUrl === null || !baseUrl || baseUrl.length > BASE_URL_MAX) {
      return { ok: false, message: "向量化接口网址需要是 https 公网地址" };
    }
    if (!model) return { ok: false, message: "兼容接口需要填写模型 ID" };
    embeddingBaseUrl = baseUrl;
  }
  const localModel = LOCAL_EMBEDDING_MODELS.some((item) => item.id === model) ? model : "";
  return {
    ok: true,
    value: {
      provider,
      model: provider === "local" ? localModel : provider === "openai" ? model : provider === "zhipu" ? "embedding-3" : "",
      baseUrl: embeddingBaseUrl,
      apiKey,
      clearKey: source.clearKey === true,
    },
  };
}

export function parseAiConfig(body: unknown): ValidationResult<AiConfigInput> {
  if (typeof body !== "object" || body === null) return { ok: false, message: "请求体格式不正确" };
  const source = body as Record<string, unknown>;
  const chat = parseEndpoint("chat", source.chat);
  if (!chat.ok) return chat;
  const reader = parseEndpoint("reader", source.reader);
  if (!reader.ok) return reader;
  const map = parseKey("map", source.map);
  if (!map.ok) return map;
  const search = parseKey("search", source.search);
  if (!search.ok) return search;
  const jw = parseJw(source.jw);
  if (!jw.ok) return jw;
  const newsUrls = parseNewsUrls(source.newsUrls);
  if (!newsUrls.ok) return newsUrls;
  const embedding = parseEmbedding(source.embedding);
  if (!embedding.ok) return embedding;
  return {
    ok: true,
    value: {
      chat: chat.value,
      reader: reader.value,
      map: map.value,
      search: search.value,
      jw: jw.value,
      newsUrls: newsUrls.value,
      embedding: embedding.value,
    },
  };
}
