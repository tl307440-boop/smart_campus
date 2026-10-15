import { readStoredUser } from "@/lib/auth/session";

/** 旧版不分账号存的会话编号，读到一次就迁到按账号的键上 */
const LEGACY_KEY = "zhiyue.threadId";
const KEY_PREFIX = "zhiyue.threadId:";
const EMBED_KEY = "zhiyue.embed.threadId";
const URL_PARAM = "tid";

function userKey(): string {
  return `${KEY_PREFIX}${readStoredUser()?.id ?? "anonymous"}`;
}

/** 会话编号优先取地址栏的 ?tid=（换浏览器时可以直接带过去），其次是这个账号上次打开的那个。 */
export function readStoredThreadId(embedded: boolean): string | null {
  if (embedded) return window.sessionStorage.getItem(EMBED_KEY);
  const fromUrl = new URLSearchParams(window.location.search).get(URL_PARAM);
  if (fromUrl) return fromUrl;
  const own = window.localStorage.getItem(userKey());
  if (own) return own;
  const legacy = window.localStorage.getItem(LEGACY_KEY);
  window.localStorage.removeItem(LEGACY_KEY);
  return legacy;
}

export function persistThreadId(threadId: string, embedded: boolean): void {
  if (embedded) {
    window.sessionStorage.setItem(EMBED_KEY, threadId);
    return;
  }
  window.localStorage.setItem(userKey(), threadId);
  const url = new URL(window.location.href);
  url.searchParams.set(URL_PARAM, threadId);
  window.history.replaceState(null, "", url);
}

/** 退出登录：忘掉这个账号的会话编号，也把地址栏里的 ?tid= 去掉，下一个人登录不会接着打开它。 */
export function forgetThreadId(): void {
  window.localStorage.removeItem(userKey());
  window.localStorage.removeItem(LEGACY_KEY);
  const url = new URL(window.location.href);
  if (!url.searchParams.has(URL_PARAM)) return;
  url.searchParams.delete(URL_PARAM);
  window.history.replaceState(null, "", url);
}
