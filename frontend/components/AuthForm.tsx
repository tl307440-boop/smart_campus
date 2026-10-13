"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { PetPortrait } from "@/components/PetPortrait";

const DEFAULT_TENANT_SLUG = "wtli";

type AuthMode = "login" | "register";

type AuthFields = {
  readonly tenantSlug: string;
  readonly studentNo: string;
  readonly password: string;
  readonly name: string;
};

type AuthFormProps = {
  readonly mode: AuthMode;
  readonly submitting: boolean;
  readonly error: string | null;
  readonly onSubmit: (fields: AuthFields) => void;
};

/* 气泡外框：viewBox 0–100 拉伸到气泡大小，尾巴伸出框外指向桌宠的嘴 */
const BUBBLE_SIDE =
  "M7 1.5C35 .6 70 2.2 93 1.5Q98.6 1.7 98.6 7C99.2 35 98.2 70 98.8 93Q98.7 98.6 93 98.5C65 99.3 30 98.2 7 98.6Q1.4 98.5 1.5 93L1.6 52L-9 45L1.8 38C1.2 25 1.9 15 1.6 7Q1.7 1.6 7 1.5Z";
const BUBBLE_TOP =
  "M7 1.5L26 1.6L33 -6.5L41 1.4C60 .8 80 2.1 93 1.5Q98.6 1.7 98.6 7C99.2 35 98.2 70 98.8 93Q98.7 98.6 93 98.5C65 99.3 30 98.2 7 98.6Q1.4 98.5 1.5 93C1 60 1.9 25 1.6 7Q1.7 1.6 7 1.5Z";

function BubbleFrame({ d, className }: { d: string; className: string }) {
  return (
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      className={`wipe-in pointer-events-none absolute inset-0 h-full w-full overflow-visible ${className}`}
      style={{ filter: "url(#ink-wobble)" }}
      aria-hidden="true"
    >
      <path
        d={d}
        fill="#ffffff"
        stroke="var(--color-ink)"
        strokeWidth={2.5}
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function Field({
  label,
  delay,
  ...input
}: { label: string; delay: number } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="rise-in block" style={{ animationDelay: `${delay}ms` }}>
      <span className="field-label">{label}</span>
      <input {...input} className="field" />
    </label>
  );
}

/** 登录/注册：桌宠大头是主角，她伸出的对话气泡就是表单；输密码时她闭眼。学校标识默认 wtli。 */
export function AuthForm({ mode, submitting, error, onSubmit }: AuthFormProps) {
  const [tenantSlug, setTenantSlug] = useState(DEFAULT_TENANT_SLUG);
  const [studentNo, setStudentNo] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [hidingEyes, setHidingEyes] = useState(false);
  const isRegister = mode === "register";

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onSubmit({ tenantSlug, studentNo, password, name });
  }

  return (
    <div className="flex min-h-dvh flex-col bg-paper">
      <header className="px-5 pt-5 lg:px-10 lg:pt-7">
        <p className="font-display text-2xl">智悦校园</p>
      </header>

      <main className="mx-auto grid w-full max-w-6xl flex-1 items-center gap-6 px-5 pt-2 pb-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:gap-0 lg:px-10 lg:pb-14">
        <div className="pop-in flex justify-center lg:justify-end lg:pr-10">
          <PetPortrait
            eyesClosed={hidingEyes}
            mood={error ? "surprised" : "normal"}
            className="h-[min(34dvh,300px)] w-auto lg:h-[min(58dvh,540px)]"
          />
        </div>

        <div className="relative mx-auto w-full max-w-[25rem] lg:mx-0 lg:max-w-[27rem]">
          <BubbleFrame d={BUBBLE_TOP} className="lg:hidden" />
          <BubbleFrame d={BUBBLE_SIDE} className="hidden lg:block" />

          <form onSubmit={handleSubmit} className="relative space-y-4 px-6 py-7 sm:px-8">
            <div className="rise-in">
              <h1 className="font-display text-[1.75rem] leading-tight lg:text-[2rem]">
                {isRegister ? "领一本草稿本" : "嗨，先告诉我你是谁"}
              </h1>
              <p className="mt-1 text-sm text-muted">
                {isRegister ? "注册学号后就能开始问" : "登录后接着问规章、资助和校园生活"}
              </p>
            </div>

            <Field
              label="学校标识"
              delay={60}
              value={tenantSlug}
              onChange={(event) => setTenantSlug(event.target.value)}
              autoComplete="organization"
            />
            <Field
              label="学号 / 工号"
              delay={120}
              value={studentNo}
              onChange={(event) => setStudentNo(event.target.value)}
              autoComplete="username"
              placeholder="校园卡上的号码"
            />
            {isRegister && (
              <Field
                label="姓名"
                delay={180}
                value={name}
                onChange={(event) => setName(event.target.value)}
                autoComplete="name"
                placeholder="怎么称呼你"
              />
            )}
            <Field
              label="密码"
              delay={isRegister ? 240 : 180}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              onFocus={() => setHidingEyes(true)}
              onBlur={() => setHidingEyes(false)}
              autoComplete={isRegister ? "new-password" : "current-password"}
            />

            {error && (
              <p role="alert" className="text-sm text-danger">
                {error}
              </p>
            )}

            <button type="submit" disabled={submitting} className="btn btn-primary w-full text-lg">
              {submitting ? "请稍候…" : isRegister ? "注册并进入" : "登录"}
            </button>

            <p className="text-center text-sm text-muted">
              {isRegister ? "已经有账号？" : "还没有账号？"}
              <Link href={isRegister ? "/login" : "/register"} className="link-ink ml-1 text-ink">
                {isRegister ? "去登录" : "领一本"}
              </Link>
            </p>
          </form>
        </div>
      </main>
    </div>
  );
}
