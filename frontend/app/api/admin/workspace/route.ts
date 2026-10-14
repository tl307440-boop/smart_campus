import { NextResponse } from "next/server";
import { getPrisma } from "@/lib/prisma";
import { requireAdmin } from "@/lib/auth/admin";
import { AUTH_ERROR, authError } from "@/lib/auth/errors";
import { type WorkspaceSettingsInput, parseWorkspaceSettings } from "@/lib/workspace/schema";

const DEFAULTS: WorkspaceSettingsInput = {
  assistantName: "小懒",
  persona: "",
  queryOnly: false,
  similarityThreshold: 0,
  topK: 3,
  counselingContact: "",
};

function toView(row: WorkspaceSettingsInput): WorkspaceSettingsInput {
  const { assistantName, persona, queryOnly, similarityThreshold, topK, counselingContact } = row;
  return { assistantName, persona, queryOnly, similarityThreshold, topK, counselingContact };
}

/** 读取本校工作区设置；还没保存过时返回默认值。 */
export async function GET(request: Request): Promise<NextResponse> {
  const check = await requireAdmin(request);
  if (!check.ok) return check.response;

  try {
    const settings = await getPrisma().tenantSettings.findUnique({
      where: { tenantId: check.tenant.id },
    });
    return NextResponse.json(toView(settings ?? DEFAULTS));
  } catch (error) {
    console.error("[admin/workspace] 读取失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "读取设置失败");
  }
}

/** 保存本校工作区设置。FastAPI 侧有 60 秒缓存，保存后最多一分钟生效。 */
export async function PUT(request: Request): Promise<NextResponse> {
  const check = await requireAdmin(request);
  if (!check.ok) return check.response;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return authError(AUTH_ERROR.INVALID_INPUT, "请求体不是合法 JSON");
  }
  const parsed = parseWorkspaceSettings(body);
  if (!parsed.ok) return authError(AUTH_ERROR.INVALID_INPUT, parsed.message);

  try {
    const saved = await getPrisma().tenantSettings.upsert({
      where: { tenantId: check.tenant.id },
      update: parsed.value,
      create: { tenantId: check.tenant.id, ...parsed.value },
    });
    return NextResponse.json(toView(saved));
  } catch (error) {
    console.error("[admin/workspace] 保存失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "保存设置失败");
  }
}
