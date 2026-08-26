"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AuthForm } from "@/components/AuthForm";
import { useAuth } from "@/lib/auth/context";

export default function RegisterPage() {
  const { register } = useAuth();
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <AuthForm
      mode="register"
      submitting={submitting}
      error={error}
      onSubmit={async ({ tenantSlug, studentNo, password, name }) => {
        setSubmitting(true);
        setError(null);
        try {
          await register({ tenantSlug, studentNo, password, name });
          router.replace("/");
        } catch (err) {
          setError(err instanceof Error ? err.message : "注册失败");
        } finally {
          setSubmitting(false);
        }
      }}
    />
  );
}
