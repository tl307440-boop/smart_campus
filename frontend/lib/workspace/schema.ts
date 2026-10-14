/**
 * 工作区设置与嵌入挂件的请求体校验。取值范围与 backend/workspace.py 的钳制保持一致。
 */
import type { ValidationResult } from "@/lib/auth/schema";

export const ASSISTANT_NAME_MAX = 16;
export const PERSONA_MAX = 800;
export const COUNSELING_CONTACT_MAX = 200;
export const TOP_K_RANGE = [1, 10] as const;
export const EMBED_NAME_MAX = 40;
export const MAX_ALLOWED_ORIGINS = 10;
export const DAILY_LIMIT_RANGE = [1, 5000] as const;

export interface WorkspaceSettingsInput {
  assistantName: string;
  persona: string;
  queryOnly: boolean;
  similarityThreshold: number;
  topK: number;
  /** 学校心理咨询的电话、地点、时间，出现在求助卡片里 */
  counselingContact: string;
}

export interface EmbedInput {
  name: string;
  allowedOrigins: string[];
  dailyLimit: number;
  enabled: boolean;
}

function asRecord(body: unknown): Record<string, unknown> | null {
  return typeof body === "object" && body !== null ? (body as Record<string, unknown>) : null;
}

export function parseWorkspaceSettings(body: unknown): ValidationResult<WorkspaceSettingsInput> {
  const source = asRecord(body);
  if (!source) return { ok: false, message: "请求体格式不正确" };

  const assistantName = typeof source.assistantName === "string" ? source.assistantName.trim() : "";
  const persona = typeof source.persona === "string" ? source.persona.trim() : "";
  const counselingContact = typeof source.counselingContact === "string" ? source.counselingContact.trim() : "";
  const threshold = Number(source.similarityThreshold);
  const topK = Number(source.topK);

  if (!assistantName || assistantName.length > ASSISTANT_NAME_MAX) {
    return { ok: false, message: `助手称呼不能为空，且不超过 ${ASSISTANT_NAME_MAX} 字` };
  }
  if (persona.length > PERSONA_MAX) {
    return { ok: false, message: `人设说明不超过 ${PERSONA_MAX} 字` };
  }
  if (counselingContact.length > COUNSELING_CONTACT_MAX) {
    return { ok: false, message: `心理咨询联系方式不超过 ${COUNSELING_CONTACT_MAX} 字` };
  }
  if (!Number.isFinite(threshold) || threshold < 0 || threshold > 1) {
    return { ok: false, message: "相似度阈值应在 0 到 1 之间" };
  }
  if (!Number.isInteger(topK) || topK < TOP_K_RANGE[0] || topK > TOP_K_RANGE[1]) {
    return { ok: false, message: `引用片段数应为 ${TOP_K_RANGE[0]}-${TOP_K_RANGE[1]} 的整数` };
  }
  return {
    ok: true,
    value: {
      assistantName,
      persona,
      queryOnly: source.queryOnly === true,
      similarityThreshold: threshold,
      topK,
      counselingContact,
    },
  };
}

/**
 * 规范成 scheme://host[:port]，与浏览器发来的 Origin 头逐字比对。
 * 只收 https，本机调试额外放行 http://localhost 和 http://127.0.0.1。
 */
export function normalizeOrigin(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  const isLocal = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (url.protocol !== "https:" && !(url.protocol === "http:" && isLocal)) return null;
  return url.origin;
}

export function parseEmbedInput(body: unknown): ValidationResult<EmbedInput> {
  const source = asRecord(body);
  if (!source) return { ok: false, message: "请求体格式不正确" };

  const name = typeof source.name === "string" ? source.name.trim() : "";
  const rawOrigins = Array.isArray(source.allowedOrigins) ? source.allowedOrigins : [];
  const dailyLimit = Number(source.dailyLimit);

  if (!name || name.length > EMBED_NAME_MAX) {
    return { ok: false, message: `挂件名称不能为空，且不超过 ${EMBED_NAME_MAX} 字` };
  }
  if (rawOrigins.length === 0 || rawOrigins.length > MAX_ALLOWED_ORIGINS) {
    return { ok: false, message: `请填写 1-${MAX_ALLOWED_ORIGINS} 个允许嵌入的网站` };
  }
  const allowedOrigins: string[] = [];
  for (const item of rawOrigins) {
    const origin = typeof item === "string" ? normalizeOrigin(item) : null;
    if (!origin) {
      return { ok: false, message: `网站地址不合法（需 https）：${String(item)}` };
    }
    if (!allowedOrigins.includes(origin)) allowedOrigins.push(origin);
  }
  if (
    !Number.isInteger(dailyLimit) ||
    dailyLimit < DAILY_LIMIT_RANGE[0] ||
    dailyLimit > DAILY_LIMIT_RANGE[1]
  ) {
    return { ok: false, message: `每日上限应为 ${DAILY_LIMIT_RANGE[0]}-${DAILY_LIMIT_RANGE[1]} 的整数` };
  }
  return { ok: true, value: { name, allowedOrigins, dailyLimit, enabled: source.enabled !== false } };
}
