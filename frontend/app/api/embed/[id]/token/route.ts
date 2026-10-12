import { NextResponse } from "next/server";
import { getPrisma } from "@/lib/prisma";
import { GUEST_TOKEN_TTL_SECONDS, signGuestToken } from "@/lib/auth/jwt";
import { clientIp, consumeRateLimit } from "@/lib/auth/rate-limit";

/**
 * 嵌入挂件的访客令牌。由第三方网页上的 public/embed.js 跨域调用。
 *
 * 浏览器发来的 Origin 头脚本改不了，这里逐字比对挂件的 allowedOrigins，
 * 防止别的网站白嫖本校额度。命令行伪造 Origin 绕得过去，所以 FastAPI 侧
 * 还有按访客 + 按挂件每日总量两道限流兜底。
 */

// 校园网常见整栋楼共用一个出口 IP，阈值要比登录宽得多
const TOKEN_LIMIT_PER_WINDOW = 60;
const TOKEN_WINDOW_MS = 15 * 60 * 1000;
const VISITOR_ID_PATTERN = /^[A-Za-z0-9-]{8,64}$/;

function corsHeaders(origin: string): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "600",
    Vary: "Origin",
  };
}

function deny(status: number, message: string, headers?: Record<string, string>): NextResponse {
  return NextResponse.json({ message }, { status, headers });
}

async function loadEmbed(id: string) {
  return getPrisma().embedConfig.findUnique({ where: { id }, include: { tenant: true } });
}

export async function OPTIONS(
  request: Request,
  ctx: RouteContext<"/api/embed/[id]/token">,
): Promise<NextResponse> {
  const origin = request.headers.get("origin") ?? "";
  const { id } = await ctx.params;
  const embed = await loadEmbed(id).catch(() => null);
  if (!embed || !embed.allowedOrigins.includes(origin)) {
    return new NextResponse(null, { status: 403 });
  }
  return new NextResponse(null, { status: 204, headers: corsHeaders(origin) });
}

export async function POST(
  request: Request,
  ctx: RouteContext<"/api/embed/[id]/token">,
): Promise<NextResponse> {
  const origin = request.headers.get("origin") ?? "";
  const { id } = await ctx.params;

  let embed;
  try {
    embed = await loadEmbed(id);
  } catch (error) {
    console.error("[embed/token] 查询失败", error);
    return deny(500, "服务暂时不可用");
  }
  if (!embed || !embed.enabled || embed.tenant.disabled) {
    return deny(404, "聊天挂件不存在或已停用");
  }
  if (!embed.allowedOrigins.includes(origin)) {
    return deny(403, "当前网站未被允许嵌入该挂件");
  }
  const headers = corsHeaders(origin);

  const limited = consumeRateLimit(
    `embed-token|${clientIp(request)}|${id}`,
    TOKEN_LIMIT_PER_WINDOW,
    TOKEN_WINDOW_MS,
  );
  if (!limited.ok) {
    return deny(429, `请求太频繁，请 ${limited.retryAfterSec} 秒后再试`, headers);
  }

  let visitorId = "";
  try {
    const body = (await request.json()) as { visitorId?: unknown };
    visitorId = typeof body.visitorId === "string" ? body.visitorId : "";
  } catch {
    // 没带访客 id 时现生成一个，只影响「同一访客跨页面续聊」
  }
  if (!VISITOR_ID_PATTERN.test(visitorId)) visitorId = crypto.randomUUID();

  const settings = await getPrisma()
    .tenantSettings.findUnique({ where: { tenantId: embed.tenantId } })
    .catch(() => null);

  const token = await signGuestToken({
    sub: `guest-${embed.id}-${visitorId}`,
    tid: embed.tenantId,
    eid: embed.id,
  });
  return NextResponse.json(
    {
      token,
      expiresIn: GUEST_TOKEN_TTL_SECONDS,
      assistantName: settings?.assistantName ?? "小懒",
      tenantName: embed.tenant.name,
    },
    { headers },
  );
}
