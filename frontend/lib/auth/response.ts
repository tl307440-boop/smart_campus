import type { Tenant, User } from "@/app/generated/prisma/client";
import { ACCESS_TOKEN_TTL_SECONDS, type UserRole, signAccessToken } from "@/lib/auth/jwt";

/** 返回给前端的用户信息，绝不包含 passwordHash。 */
export interface AuthUser {
  id: string;
  studentNo: string;
  name: string;
  role: UserRole;
  tenantSlug: string;
  tenantName: string;
}

export interface AuthSuccessBody {
  accessToken: string;
  expiresIn: number;
  user: AuthUser;
}

export function toAuthUser(user: User, tenant: Tenant): AuthUser {
  return {
    id: user.id,
    studentNo: user.studentNo,
    name: user.name,
    role: user.role,
    tenantSlug: tenant.slug,
    tenantName: tenant.name,
  };
}

export async function toAuthSuccess(user: User, tenant: Tenant): Promise<AuthSuccessBody> {
  const accessToken = await signAccessToken({
    sub: user.id,
    tid: tenant.id,
    sno: user.studentNo,
    name: user.name,
    role: user.role,
  });
  return {
    accessToken,
    expiresIn: ACCESS_TOKEN_TTL_SECONDS,
    user: toAuthUser(user, tenant),
  };
}
