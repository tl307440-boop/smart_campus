"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AuthForm } from "@/components/AuthForm";
import { useAuth } from "@/lib/auth/context";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <AuthForm
      mode="login"
      submitting={submitting}
      error={error}
      onSubmit={async ({ tenantSlug, studentNo, password }) => {
        setSubmitting(true);
        setError(null);
        try {
          await login({ tenantSlug, studentNo, password });
          router.replace("/");
        } catch (err) {
          setError(err instanceof Error ? err.message : "登录失败");
        } finally {
          setSubmitting(false);
        }
      }}
    />
  );
}
