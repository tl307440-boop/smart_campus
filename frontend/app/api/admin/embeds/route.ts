import { NextResponse } from "next/server";
import { getPrisma } from "@/lib/prisma";
import { requireAdmin } from "@/lib/auth/admin";
import { AUTH_ERROR, authError } from "@/lib/auth/errors";
import { parseEmbedInput } from "@/lib/workspace/schema";

/** 一所学校最多挂几个挂件，防止误操作批量创建 */
const MAX_EMBEDS_PER_TENANT = 20;

export async function GET(request: Request): Promise<NextResponse> {
  const check = await requireAdmin(request);
  if (!check.ok) return check.response;

  try {
    const embeds = await getPrisma().embedConfig.findMany({
      where: { tenantId: check.tenant.id },
      orderBy: { createdAt: "desc" },
    });
    return NextResponse.json({ embeds });
  } catch (error) {
    console.error("[admin/embeds] 读取失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "读取挂件失败");
  }
}

export async function POST(request: Request): Promise<NextResponse> {
  const check = await requireAdmin(request);
  if (!check.ok) return check.response;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return authError(AUTH_ERROR.INVALID_INPUT, "请求体不是合法 JSON");
  }
  const parsed = parseEmbedInput(body);
  if (!parsed.ok) return authError(AUTH_ERROR.INVALID_INPUT, parsed.message);

  try {
    const prisma = getPrisma();
    const count = await prisma.embedConfig.count({ where: { tenantId: check.tenant.id } });
    if (count >= MAX_EMBEDS_PER_TENANT) {
      return authError(AUTH_ERROR.INVALID_INPUT, `每所学校最多 ${MAX_EMBEDS_PER_TENANT} 个挂件`);
    }
    const embed = await prisma.embedConfig.create({
      data: { tenantId: check.tenant.id, ...parsed.value },
    });
    return NextResponse.json({ embed }, { status: 201 });
  } catch (error) {
    console.error("[admin/embeds] 创建失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "创建挂件失败");
  }
}
