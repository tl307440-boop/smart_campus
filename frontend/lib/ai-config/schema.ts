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

export type AiConfigInput = Record<AiService, AiEndpointInput>;

export interface AiEndpointView {
  baseUrl: string;
  model: string;
  hasKey: boolean;
  keyHint: string;
}

export type AiConfigView = Record<AiService, AiEndpointView>;

const SERVICE_NAME: Record<AiService, string> = { chat: "对话模型", reader: "长文档精读" };

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

export function parseAiConfig(body: unknown): ValidationResult<AiConfigInput> {
  if (typeof body !== "object" || body === null) return { ok: false, message: "请求体格式不正确" };
  const source = body as Record<string, unknown>;
  const chat = parseEndpoint("chat", source.chat);
  if (!chat.ok) return chat;
  const reader = parseEndpoint("reader", source.reader);
  if (!reader.ok) return reader;
  return { ok: true, value: { chat: chat.value, reader: reader.value } };
}
