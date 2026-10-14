/**
 * 学校站点上的 /desk 只是一扇门，把请求原样交给旁边的小本子进程。
 * 登录、在线和记录都在那个进程里，不进聊天页面。
 */

const HOP = new Set(["connection", "content-encoding", "content-length", "transfer-encoding"]);

function upstream(): string {
  return (process.env.DESK_UPSTREAM || "http://127.0.0.1:8300").replace(/\/$/, "");
}

function forwarded(response: Response): Response {
  const out = new Headers();
  response.headers.forEach((value, key) => {
    if (!HOP.has(key.toLowerCase()) && key.toLowerCase() !== "set-cookie") out.append(key, value);
  });
  for (const cookie of response.headers.getSetCookie?.() ?? []) out.append("set-cookie", cookie);
  return new Response(response.body, { status: response.status, headers: out });
}

/** 小本子把 /desk 转到 /desk/，站点又会把末尾斜杠去掉，两边对着跳。这一跳在门里走完。 */
async function through(request: Request): Promise<Response> {
  const incoming = new URL(request.url);
  let pathname = incoming.pathname.startsWith("/desk") ? incoming.pathname : "/desk";
  const headers = new Headers(request.headers);
  headers.delete("host");
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();
  for (let hop = 0; hop < 3; hop++) {
    const target = new URL(`${upstream()}${pathname}`);
    target.search = incoming.search;
    let response: Response;
    try {
      response = await fetch(target, { method: request.method, headers, body, redirect: "manual" });
    } catch {
      return new Response("后台暂时连不上", { status: 502 });
    }
    if (request.method === "GET" && response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location") ?? "";
      const next = (location.startsWith("http") ? new URL(location).pathname : location.split("?")[0]) || "";
      if (next.startsWith("/desk") && next !== pathname) {
        pathname = next;
        continue;
      }
    }
    return forwarded(response);
  }
  return new Response("后台暂时连不上", { status: 502 });
}

export function GET(request: Request): Promise<Response> {
  return through(request);
}

export function POST(request: Request): Promise<Response> {
  return through(request);
}

export function HEAD(request: Request): Promise<Response> {
  return through(request);
}
