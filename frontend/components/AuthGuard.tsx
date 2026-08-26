"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth/context";

const PUBLIC_PATHS = new Set(["/login", "/register"]);

/**
 * 未登录只能看登录/注册；已登录再进这两页则送回聊天。
 * 身份以 Next 签发、本地保存的 JWT 为准，不再用匿名 userId。
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.has(pathname);

  useEffect(() => {
    if (!ready) return;
    if (!user && !isPublic) {
      router.replace("/login");
      return;
    }
    if (user && isPublic) {
      router.replace("/");
    }
  }, [ready, user, isPublic, router]);

  if (!ready) {
    return (
      <div className="flex min-h-dvh items-center justify-center text-sm text-muted">
        正在确认登录状态…
      </div>
    );
  }

  if (!user && !isPublic) {
    return (
      <div className="flex min-h-dvh items-center justify-center text-sm text-muted">
        正在前往登录…
      </div>
    );
  }

  if (user && isPublic) {
    return (
      <div className="flex min-h-dvh items-center justify-center text-sm text-muted">
        已登录，正在进入对话…
      </div>
    );
  }

  return <>{children}</>;
}
