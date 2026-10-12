import type { NextResponse } from "next/server";
import type { Tenant, User } from "@/app/generated/prisma/client";
import { getPrisma } from "@/lib/prisma";
import { AUTH_ERROR, type AuthErrorBody, authError } from "@/lib/auth/errors";
import { USER_ROLE, verifyAccessToken } from "@/lib/auth/jwt";

const BEARER_PREFIX = "Bearer ";

export type AdminCheck =
  | { ok: true; user: User; tenant: Tenant }
  | { ok: false; response: NextResponse<AuthErrorBody> };

/**
 * 管理接口的统一门禁：令牌有效 + 回查数据库确认仍是本校 ADMIN。
 * 角色以数据库为准而不是令牌里的 claim，降级后旧令牌立即失去管理权限。
 */
export async function requireAdmin(request: Request): Promise<AdminCheck> {
  const header = request.headers.get("authorization") ?? "";
  if (!header.startsWith(BEARER_PREFIX)) {
    return { ok: false, response: authError(AUTH_ERROR.UNAUTHORIZED, "缺少访问令牌") };
  }
  const claims = await verifyAccessToken(header.slice(BEARER_PREFIX.length));
  if (!claims) {
    return { ok: false, response: authError(AUTH_ERROR.UNAUTHORIZED, "登录状态已失效，请重新登录") };
  }

  const user = await getPrisma().user.findUnique({
    where: { id: claims.sub },
    include: { tenant: true },
  });
  if (!user || user.disabled || user.tenant.disabled) {
    return { ok: false, response: authError(AUTH_ERROR.UNAUTHORIZED, "账号不可用，请重新登录") };
  }
  if (user.role !== USER_ROLE.ADMIN) {
    return { ok: false, response: authError(AUTH_ERROR.FORBIDDEN, "需要管理员权限") };
  }
  return { ok: true, user, tenant: user.tenant };
}
