/**
 * 学校站点上的 /desk 只是一扇门，把请求原样交给旁边的小本子进程。
 * 登录、在线和记录都在那个进程里，不进聊天页面。
 */

const HOP = new Set(["connection", "content-encoding", "content-length", "transfer-encoding"]);

function upstream(): string {
  return (process.env.DESK_UPSTREAM || "http://127.0.0.1:8300").replace(/\/$/, "");
}

async function through(request: Request, path: string[] | undefined): Promise<Response> {
  const tail = path?.length ? `/${path.map(encodeURIComponent).join("/")}` : "";
  const target = new URL(`${upstream()}/desk${tail}`);
  target.search = new URL(request.url).search;
  const headers = new Headers(request.headers);
  headers.delete("host");
  const init: RequestInit = { method: request.method, headers, redirect: "manual" };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }
  let response: Response;
  try {
    response = await fetch(target, init);
  } catch {
    return new Response("后台暂时连不上", { status: 502 });
  }
  const out = new Headers();
  response.headers.forEach((value, key) => {
    if (!HOP.has(key.toLowerCase()) && key.toLowerCase() !== "set-cookie") out.append(key, value);
  });
  for (const cookie of response.headers.getSetCookie?.() ?? []) out.append("set-cookie", cookie);
  return new Response(response.body, { status: response.status, headers: out });
}

export function GET(request: Request, ctx: RouteContext<"/desk/[[...path]]">): Promise<Response> {
  return ctx.params.then(({ path }) => through(request, path));
}

export function POST(request: Request, ctx: RouteContext<"/desk/[[...path]]">): Promise<Response> {
  return ctx.params.then(({ path }) => through(request, path));
}

export function HEAD(request: Request, ctx: RouteContext<"/desk/[[...path]]">): Promise<Response> {
  return ctx.params.then(({ path }) => through(request, path));
}
