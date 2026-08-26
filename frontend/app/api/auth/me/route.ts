import { NextResponse } from "next/server";
import { getPrisma } from "@/lib/prisma";
import { AUTH_ERROR, authError } from "@/lib/auth/errors";
import { verifyAccessToken } from "@/lib/auth/jwt";
import { toAuthUser } from "@/lib/auth/response";

const BEARER_PREFIX = "Bearer ";

export async function GET(request: Request): Promise<NextResponse> {
  const header = request.headers.get("authorization") ?? "";
  if (!header.startsWith(BEARER_PREFIX)) {
    return authError(AUTH_ERROR.UNAUTHORIZED, "缺少访问令牌");
  }

  const claims = await verifyAccessToken(header.slice(BEARER_PREFIX.length));
  if (!claims) {
    return authError(AUTH_ERROR.UNAUTHORIZED, "登录状态已失效，请重新登录");
  }

  try {
    // 令牌本身可能还没过期，但用户已被停用或删除，所以要回查数据库
    const user = await getPrisma().user.findUnique({
      where: { id: claims.sub },
      include: { tenant: true },
    });
    if (!user || user.disabled || user.tenant.disabled) {
      return authError(AUTH_ERROR.UNAUTHORIZED, "账号不可用，请重新登录");
    }
    return NextResponse.json({ user: toAuthUser(user, user.tenant) });
  } catch (error) {
    console.error("[auth/me] 查询失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "服务暂时不可用");
  }
}
