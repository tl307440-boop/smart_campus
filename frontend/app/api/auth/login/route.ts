import { NextResponse } from "next/server";
import { getPrisma } from "@/lib/prisma";
import { AUTH_ERROR, authError } from "@/lib/auth/errors";
import { burnTiming, verifyPassword } from "@/lib/auth/password";
import { parseLoginInput } from "@/lib/auth/schema";
import { toAuthSuccess } from "@/lib/auth/response";
import { clientIp, consumeRateLimit, rateLimitKey } from "@/lib/auth/rate-limit";

export async function POST(request: Request): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return authError(AUTH_ERROR.INVALID_INPUT, "请求体不是合法 JSON");
  }

  const parsed = parseLoginInput(body);
  if (!parsed.ok) {
    return authError(AUTH_ERROR.INVALID_INPUT, parsed.message);
  }
  const { tenantSlug, studentNo, password } = parsed.value;
  const limited = consumeRateLimit(rateLimitKey(clientIp(request), studentNo));
  if (!limited.ok) {
    return authError(AUTH_ERROR.RATE_LIMITED, `尝试次数过多，请 ${limited.retryAfterSec} 秒后再试`);
  }

  try {
    const prisma = getPrisma();
    const tenant = await prisma.tenant.findUnique({ where: { slug: tenantSlug } });
    if (!tenant || tenant.disabled) {
      await burnTiming(password);
      return authError(AUTH_ERROR.INVALID_CREDENTIALS, "学校标识、学号或密码不正确");
    }

    const user = await prisma.user.findUnique({
      where: { tenantId_studentNo: { tenantId: tenant.id, studentNo } },
    });
    if (!user) {
      // 账号不存在时也跑一遍散列校验，让响应耗时与密码错误时相当
      await burnTiming(password);
      return authError(AUTH_ERROR.INVALID_CREDENTIALS, "学校标识、学号或密码不正确");
    }
    if (!(await verifyPassword(password, user.passwordHash))) {
      return authError(AUTH_ERROR.INVALID_CREDENTIALS, "学校标识、学号或密码不正确");
    }
    if (user.disabled) {
      return authError(AUTH_ERROR.ACCOUNT_DISABLED, "账号已被停用，请联系管理员");
    }

    const updated = await prisma.user.update({
      where: { id: user.id },
      data: { lastLoginAt: new Date() },
    });
    return NextResponse.json(await toAuthSuccess(updated, tenant));
  } catch (error) {
    console.error("[auth/login] 登录失败", error);
    return authError(AUTH_ERROR.SERVER_ERROR, "登录失败，请稍后再试");
  }
}
