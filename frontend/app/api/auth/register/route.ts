import { NextResponse } from "next/server";
import { getPrisma } from "@/lib/prisma";
import { AUTH_ERROR, authError } from "@/lib/auth/errors";
import { hashPassword } from "@/lib/auth/password";
import { parseRegisterInput } from "@/lib/auth/schema";
import { type AuthSuccessBody, toAuthSuccess } from "@/lib/auth/response";
import { clientIp, consumeRateLimit, rateLimitKey } from "@/lib/auth/rate-limit";

/** Prisma 唯一约束冲突 */
const UNIQUE_VIOLATION = "P2002";

export async function POST(request: Request): Promise<NextResponse<AuthSuccessBody | unknown>> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return authError(AUTH_ERROR.INVALID_INPUT, "请求体不是合法 JSON");
  }

  const parsed = parseRegisterInput(body);
  if (!parsed.ok) {
    return authError(AUTH_ERROR.INVALID_INPUT, parsed.message);
  }
  const { tenantSlug, studentNo, password, name } = parsed.value;
  const limited = consumeRateLimit(rateLimitKey(clientIp(request), studentNo));
  if (!limited.ok) {
    return authError(AUTH_ERROR.RATE_LIMITED, `尝试次数过多，请 ${limited.retryAfterSec} 秒后再试`);
  }

  const prisma = getPrisma();
  const tenant = await prisma.tenant.findUnique({ where: { slug: tenantSlug } });
  if (!tenant) {
    return authError(AUTH_ERROR.TENANT_NOT_FOUND, "找不到这所学校，请确认学校标识");
  }
  if (tenant.disabled) {
    return authError(AUTH_ERROR.ACCOUNT_DISABLED, "该学校已停用");
  }

  try {
    const user = await prisma.user.create({
      data: {
        tenantId: tenant.id,
        studentNo,
        name,
        passwordHash: await hashPassword(password),
      },
    });
    return NextResponse.json(await toAuthSuccess(user, tenant), { status: 201 });
  } catch (error) {
    if (typeof error === "object" && error !== null && "code" in error) {
      if ((error as { code: unknown }).code === UNIQUE_VIOLATION) {
        return authError(AUTH_ERROR.ALREADY_EXISTS, "该学号已注册，请直接登录");
      }
    }
    console.error("[auth/register] 注册失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "注册失败，请稍后再试");
  }
}
