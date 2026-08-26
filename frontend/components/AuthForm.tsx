"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { CampusStill } from "@/components/CampusStill";
import {
  AUTH_STRIP_IDS,
  CAMPUS_SCENES,
  SCENE_PLACEMENT,
  SCHOOL_MARK,
  type CampusSceneId,
} from "@/lib/campus/scenes";

const FIELD =
  "w-full rounded-lg border border-[#c9a36a]/50 bg-white px-3 py-2 text-sm text-[#1a1208] outline-none placeholder:text-[#8a8278] focus:border-[#b8863a] focus:bg-white";

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

/** 登录/注册：风光占视口左侧，表单靠右成实色窄栏，不压主楼。学校标识默认 wtli。 */
export function AuthForm({ mode, submitting, error, onSubmit }: AuthFormProps) {
  const [tenantSlug, setTenantSlug] = useState(DEFAULT_TENANT_SLUG);
  const [studentNo, setStudentNo] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [sceneId, setSceneId] = useState<CampusSceneId>(SCENE_PLACEMENT.authHero);

  const scene = CAMPUS_SCENES[sceneId];
  const isRegister = mode === "register";

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onSubmit({ tenantSlug, studentNo, password, name });
  }

  return (
    <div className="flex min-h-dvh flex-col bg-[#f4efe4] lg:flex-row">
      <section className="relative min-h-[46vh] flex-1 overflow-hidden bg-[#d8c9a8] lg:min-h-dvh">
        <CampusStill
          scene={scene}
          sizes="(min-width: 1024px) 72vw, 100vw"
          priority
          imageClassName="origin-[48%_42%] object-[center_38%] animate-campus-kenburns"
        />
        <div
          className="absolute inset-0 bg-linear-to-t from-[#1a1208]/55 via-[#1a1208]/10 to-black/15"
          aria-hidden
        />
        <div
          className="absolute inset-y-0 right-0 hidden w-24 bg-linear-to-l from-[#f4efe4]/40 to-transparent lg:block"
          aria-hidden
        />

        <div className="absolute inset-x-0 top-0 z-10 px-5 pt-7 text-white drop-shadow-[0_2px_16px_rgba(0,0,0,0.45)] lg:px-10 lg:pt-10">
          <p className="text-xs tracking-[0.28em] text-[#f0d9a8]">{SCHOOL_MARK.place}</p>
          <p className="mt-2 font-display text-3xl font-bold tracking-[0.12em] lg:text-5xl">
            {SCHOOL_MARK.name}
          </p>
          <p className="mt-2 text-sm tracking-[0.22em] text-[#f0d9a8]">{SCHOOL_MARK.motto}</p>
          <p className="mt-4 max-w-md text-sm text-white/90">
            {scene.title}
            <span className="text-white/70"> · {scene.caption}</span>
          </p>
        </div>

        <div className="absolute inset-x-0 bottom-0 z-10 flex gap-2 px-5 pb-4 lg:px-10 lg:pb-8">
          {AUTH_STRIP_IDS.map((id) => {
            const thumb = CAMPUS_SCENES[id];
            const active = id === sceneId;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setSceneId(id)}
                className={`relative h-11 w-[4.1rem] overflow-hidden rounded-lg border transition lg:h-12 lg:w-[4.5rem] ${
                  active
                    ? "border-[#f0d9a8] ring-1 ring-[#d4a35a]/70"
                    : "border-white/35 hover:border-[#f0d9a8]"
                }`}
                aria-label={`查看${thumb.title}`}
                aria-pressed={active}
              >
                <CampusStill
                  scene={thumb}
                  sizes="72px"
                  decorative
                  imageClassName="object-cover"
                />
              </button>
            );
          })}
        </div>
      </section>

      <aside className="relative z-10 flex w-full shrink-0 flex-col justify-center border-t border-[#d4a35a]/25 bg-[#f4efe4] px-6 py-6 text-[#1a1208] lg:h-dvh lg:w-[22.5rem] lg:overflow-y-auto lg:border-t-0 lg:border-l lg:px-7 xl:w-[24rem]">
        <div className="animate-fade-in">
          <p className="font-display text-xl font-bold tracking-[0.08em]">
            智悦校园 · <span className="text-[#b8863a]">小懒</span>
          </p>
          <p className="mt-1.5 text-sm text-[#6b6358]">
            {isRegister ? "注册学号后即可开始对话" : "登录后继续校园问答"}
          </p>

          <form onSubmit={handleSubmit} className="mt-6 space-y-3">
            <label className="block space-y-1">
              <span className="text-xs tracking-[0.12em] text-[#7a7268] uppercase">学校标识</span>
              <input
                value={tenantSlug}
                onChange={(event) => setTenantSlug(event.target.value)}
                autoComplete="organization"
                className={FIELD}
              />
            </label>

            <label className="block space-y-1">
              <span className="text-xs tracking-[0.12em] text-[#7a7268] uppercase">学号 / 工号</span>
              <input
                value={studentNo}
                onChange={(event) => setStudentNo(event.target.value)}
                autoComplete="username"
                placeholder="如 admin001"
                className={FIELD}
              />
            </label>

            {isRegister && (
              <label className="block space-y-1">
                <span className="text-xs tracking-[0.12em] text-[#7a7268] uppercase">姓名</span>
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  autoComplete="name"
                  placeholder="怎么称呼你"
                  className={FIELD}
                />
              </label>
            )}

            <label className="block space-y-1">
              <span className="text-xs tracking-[0.12em] text-[#7a7268] uppercase">密码</span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete={isRegister ? "new-password" : "current-password"}
                className={FIELD}
              />
            </label>

            {error && (
              <p className="rounded-lg border border-danger/40 bg-white px-3 py-2 text-sm text-danger">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="mt-1 h-10 w-full rounded-full bg-linear-to-br from-[#c8954a] to-[#d4a35a] text-sm font-semibold text-[#1a1208] transition hover:brightness-110 disabled:opacity-50"
            >
              {submitting ? "请稍候…" : isRegister ? "注册并进入" : "登录"}
            </button>
          </form>

          <p className="mt-5 text-center text-sm text-[#6b6358]">
            {isRegister ? (
              <>
                已有账号？{" "}
                <Link href="/login" className="text-[#b8863a] hover:underline">
                  去登录
                </Link>
              </>
            ) : (
              <>
                还没有账号？{" "}
                <Link href="/register" className="text-[#b8863a] hover:underline">
                  注册学号
                </Link>
              </>
            )}
          </p>
        </div>
      </aside>
    </div>
  );
}
