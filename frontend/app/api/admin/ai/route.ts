import { NextResponse } from "next/server";
import type { TenantAiConfig } from "@/app/generated/prisma/client";
import { getPrisma } from "@/lib/prisma";
import { requireAdmin } from "@/lib/auth/admin";
import { AUTH_ERROR, authError } from "@/lib/auth/errors";
import { encryptSecret, keyHint } from "@/lib/ai-config/secret-box";
import { type AiConfigView, type AiEndpointInput, parseAiConfig } from "@/lib/ai-config/schema";

type Row = Pick<
  TenantAiConfig,
  | "chatBaseUrl"
  | "chatModel"
  | "chatKey"
  | "chatKeyHint"
  | "readerBaseUrl"
  | "readerModel"
  | "readerKey"
  | "readerKeyHint"
>;

const EMPTY: Row = {
  chatBaseUrl: "",
  chatModel: "",
  chatKey: "",
  chatKeyHint: "",
  readerBaseUrl: "",
  readerModel: "",
  readerKey: "",
  readerKeyHint: "",
};

/** 读回时密文一律不出服务端，只说有没有、末四位是什么 */
function toView(row: Row): AiConfigView {
  return {
    chat: { baseUrl: row.chatBaseUrl, model: row.chatModel, hasKey: Boolean(row.chatKey), keyHint: row.chatKeyHint },
    reader: {
      baseUrl: row.readerBaseUrl,
      model: row.readerModel,
      hasKey: Boolean(row.readerKey),
      keyHint: row.readerKeyHint,
    },
  };
}

/** 填了新密钥就换，勾了清除就清，都没有就保持原样（不出现在 update 里） */
function keyChange(input: AiEndpointInput): { key: string; hint: string } | null {
  if (input.clearKey) return { key: "", hint: "" };
  if (input.apiKey) return { key: encryptSecret(input.apiKey), hint: keyHint(input.apiKey) };
  return null;
}

export async function GET(request: Request): Promise<NextResponse> {
  const check = await requireAdmin(request);
  if (!check.ok) return check.response;

  try {
    const row = await getPrisma().tenantAiConfig.findUnique({ where: { tenantId: check.tenant.id } });
    return NextResponse.json(toView(row ?? EMPTY));
  } catch (error) {
    console.error("[admin/ai] 读取失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "读取 AI 服务配置失败");
  }
}

/** 保存本校 AI 服务配置。FastAPI 侧有 60 秒缓存；前端保存后紧接着调连通测试，会顺带刷新它。 */
export async function PUT(request: Request): Promise<NextResponse> {
  const check = await requireAdmin(request);
  if (!check.ok) return check.response;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return authError(AUTH_ERROR.INVALID_INPUT, "请求体不是合法 JSON");
  }
  const parsed = parseAiConfig(body);
  if (!parsed.ok) return authError(AUTH_ERROR.INVALID_INPUT, parsed.message);

  const { chat, reader } = parsed.value;
  try {
    const chatKey = keyChange(chat);
    const readerKey = keyChange(reader);
    const data = {
      chatBaseUrl: chat.baseUrl,
      chatModel: chat.model,
      readerBaseUrl: reader.baseUrl,
      readerModel: reader.model,
      ...(chatKey && { chatKey: chatKey.key, chatKeyHint: chatKey.hint }),
      ...(readerKey && { readerKey: readerKey.key, readerKeyHint: readerKey.hint }),
    };
    const saved = await getPrisma().tenantAiConfig.upsert({
      where: { tenantId: check.tenant.id },
      update: data,
      create: { tenantId: check.tenant.id, ...data },
    });
    return NextResponse.json(toView(saved));
  } catch (error) {
    console.error("[admin/ai] 保存失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "保存 AI 服务配置失败");
  }
}
