"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth/context";

function MagicLandingInner() {
  const params = useSearchParams();
  const router = useRouter();
  const { loginWithMagic } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const ran = useRef(false);
  const token = params.get("t")?.trim() ?? "";

  useEffect(() => {
    if (ran.current || !token) return;
    ran.current = true;

    const rawNext = params.get("next")?.trim() ?? "/";
    const nextPath = rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/";

    // 先从地址栏抹掉 token，避免留在浏览记录里
    if (typeof window !== "undefined") {
      window.history.replaceState(null, "", "/magic");
    }

    loginWithMagic(token)
      .then(() => {
        router.replace(nextPath);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "链接已失效，请在企微回复「减压」重新获取");
      });
  }, [params, token, router, loginWithMagic]);

  const displayError = !token
    ? "免登参数缺失，请返回登录页或在企微中重新发送「减压」。"
    : error;

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <div className="sketch w-full max-w-sm bg-paper p-6 text-center space-y-4">
        <h1 className="font-display text-2xl">正在进入智悦手机伴侣…</h1>
        {displayError ? (
          <>
            <p className="text-sm text-danger" role="alert">
              {displayError}
            </p>
            <Link href="/login" className="btn btn-primary w-full">
              前往账号登录
            </Link>
          </>
        ) : (
          <p className="text-sm text-muted">免密验证中，请稍候一小下～</p>
        )}
      </div>
    </main>
  );
}

export default function MagicPage() {
  return (
    <Suspense
      fallback={
        <main className="flex min-h-dvh items-center justify-center text-sm text-muted">
          正在打开…
        </main>
      }
    >
      <MagicLandingInner />
    </Suspense>
  );
}
