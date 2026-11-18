import { NextResponse } from "next/server";
import { getPrisma } from "@/lib/prisma";
import { AUTH_ERROR, authError } from "@/lib/auth/errors";
import { verifyMagicToken } from "@/lib/auth/jwt";
import { toAuthSuccess } from "@/lib/auth/response";

export async function POST(request: Request): Promise<NextResponse> {
  let token = "";
  try {
    const body = (await request.json()) as { token?: unknown };
    if (typeof body?.token === "string") token = body.token.trim();
  } catch {
    return authError(AUTH_ERROR.INVALID_INPUT, "请求体不是合法 JSON");
  }
  if (!token) {
    return authError(AUTH_ERROR.INVALID_INPUT, "缺少免登令牌");
  }

  const claims = await verifyMagicToken(token);
  if (!claims) {
    return authError(AUTH_ERROR.UNAUTHORIZED, "链接已过期或无效，请在企微回复「减压」重新获取");
  }

  try {
    const user = await getPrisma().user.findUnique({
      where: { id: claims.sub },
      include: { tenant: true },
    });
    if (!user || user.tenantId !== claims.tid || user.disabled || user.tenant.disabled) {
      return authError(AUTH_ERROR.UNAUTHORIZED, "账号不可用，请联系管理员");
    }
    const updated = await getPrisma().user.update({
      where: { id: user.id },
      data: { lastLoginAt: new Date() },
    });
    return NextResponse.json(await toAuthSuccess(updated, user.tenant));
  } catch (error) {
    console.error("[auth/magic] 免登换票失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "免登失败，请稍后再试");
  }
}
