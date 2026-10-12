"use client";

import { useEffect, useSyncExternalStore } from "react";
import { EmbedChat } from "@/components/EmbedChat";
import { setAccessTokenOverride } from "@/lib/api";

interface EmbedSession {
  assistantName: string;
  tenantName: string;
}

/**
 * 访客令牌由 public/embed.js 在宿主网页上申请后放进 URL hash，
 * hash 不会发给服务器，也不会出现在访问日志里。只解析一次：随后会从地址栏抹掉。
 */
let captured: EmbedSession | null | undefined;

function readSession(): EmbedSession | null {
  if (captured !== undefined) return captured;
  const params = new URLSearchParams(window.location.hash.slice(1));
  const token = params.get("token");
  if (token) setAccessTokenOverride(token);
  captured = token
    ? { assistantName: params.get("name") || "小懒", tenantName: params.get("school") || "" }
    : null;
  return captured;
}

function subscribe(): () => void {
  return () => {};
}

export default function EmbedPage() {
  // 服务端渲染时拿不到 hash，返回 undefined 表示「还没读」
  const session = useSyncExternalStore(subscribe, readSession, () => undefined);

  useEffect(() => {
    // 令牌已经读进内存，从地址栏抹掉，避免被访客复制分享出去
    if (session) window.history.replaceState(null, "", window.location.pathname);
  }, [session]);

  if (session === undefined) return null;
  if (session === null) {
    return (
      <div className="flex h-dvh items-center justify-center p-6 text-center text-sm text-muted">
        请通过学校网站右下角的聊天气泡打开。
      </div>
    );
  }
  return <EmbedChat assistantName={session.assistantName} tenantName={session.tenantName} />;
}
