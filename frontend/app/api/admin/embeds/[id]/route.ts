import { NextResponse } from "next/server";
import { getPrisma } from "@/lib/prisma";
import { requireAdmin } from "@/lib/auth/admin";
import { AUTH_ERROR, authError } from "@/lib/auth/errors";
import { parseEmbedInput } from "@/lib/workspace/schema";

/** 只认本校的挂件：按 (id, tenantId) 一起查，拿到别校的 id 也改不了 */
async function ownEmbed(id: string, tenantId: string) {
  return getPrisma().embedConfig.findFirst({ where: { id, tenantId } });
}

export async function PATCH(
  request: Request,
  ctx: RouteContext<"/api/admin/embeds/[id]">,
): Promise<NextResponse> {
  const check = await requireAdmin(request);
  if (!check.ok) return check.response;
  const { id } = await ctx.params;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return authError(AUTH_ERROR.INVALID_INPUT, "请求体不是合法 JSON");
  }
  const parsed = parseEmbedInput(body);
  if (!parsed.ok) return authError(AUTH_ERROR.INVALID_INPUT, parsed.message);

  try {
    if (!(await ownEmbed(id, check.tenant.id))) {
      return authError(AUTH_ERROR.NOT_FOUND, "挂件不存在");
    }
    const embed = await getPrisma().embedConfig.update({ where: { id }, data: parsed.value });
    return NextResponse.json({ embed });
  } catch (error) {
    console.error("[admin/embeds] 更新失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "更新挂件失败");
  }
}

export async function DELETE(
  request: Request,
  ctx: RouteContext<"/api/admin/embeds/[id]">,
): Promise<NextResponse> {
  const check = await requireAdmin(request);
  if (!check.ok) return check.response;
  const { id } = await ctx.params;

  try {
    if (!(await ownEmbed(id, check.tenant.id))) {
      return authError(AUTH_ERROR.NOT_FOUND, "挂件不存在");
    }
    await getPrisma().embedConfig.delete({ where: { id } });
    return NextResponse.json({ status: "deleted" });
  } catch (error) {
    console.error("[admin/embeds] 删除失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "删除挂件失败");
  }
}
