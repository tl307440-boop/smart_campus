"use client";

import { useEffect, useRef, useState } from "react";
import { NewPageIcon, PouchIcon, SpeakerIcon } from "@/components/SketchIcons";
import type { AuthUser } from "@/lib/auth/session";

interface Props {
  assistantName: string;
  user: AuthUser | null;
  /** 老师/管理员：账户菜单里显示会话编号，方便对日志 */
  isStaff: boolean;
  threadId: string | null;
  ttsEnabled: boolean;
  onToggleTts: (enabled: boolean) => void;
  onNewSession: () => void;
  onOpenTools: () => void;
  onLogout: () => void;
}

function AccountMenu({ user, isStaff, threadId, ttsEnabled, onToggleTts, onLogout }: Pick<
  Props,
  "user" | "isStaff" | "threadId" | "ttsEnabled" | "onToggleTts" | "onLogout"
>) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!user) return null;
  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={`账户：${user.name}`}
        className="btn btn-icon rounded-full bg-skin text-lg"
      >
        {user.name.slice(0, 1)}
      </button>
      {open && (
        <div className="sketch rise-in absolute top-12 right-0 z-40 w-64 bg-paper p-4 [animation-duration:180ms]">
          <p className="font-display text-lg leading-tight">{user.name}</p>
          <p className="text-sm text-muted">
            {user.tenantName} · {user.studentNo}
          </p>
          <label className="mt-3 flex cursor-pointer items-center justify-between gap-3 border-t border-dashed border-rule pt-3">
            <span className="flex items-center gap-2">
              <SpeakerIcon on={ttsEnabled} size={18} />
              朗读回答
            </span>
            <input
              type="checkbox"
              checked={ttsEnabled}
              onChange={(event) => onToggleTts(event.target.checked)}
              className="size-4 accent-pink"
            />
          </label>
          {isStaff && threadId && (
            <p className="mt-2 text-[0.8125rem] break-all text-muted">会话 {threadId}</p>
          )}
          <button type="button" onClick={onLogout} className="link-ink mt-3 text-[0.9375rem]">
            退出登录
          </button>
        </div>
      )}
    </div>
  );
}

/** 纸页页头：助手名 + 学校，右边是新的一页、工具（窄屏）和账户菜单。 */
export function ChatHeader({ assistantName, user, onNewSession, onOpenTools, ...menu }: Props) {
  return (
    <header className="flex items-center gap-2 border-b-2 border-ink px-4 py-2.5 sm:px-6">
      <div className="min-w-0 flex-1">
        <p className="truncate font-display text-xl leading-tight sm:text-2xl">{assistantName}</p>
        {user && <p className="truncate text-[0.8125rem] text-muted">{user.tenantName} · 智悦校园</p>}
      </div>
      <button type="button" onClick={onNewSession} className="btn btn-sm" aria-label="新的一页">
        <NewPageIcon size={18} />
        <span className="hidden sm:inline">新的一页</span>
      </button>
      <button type="button" onClick={onOpenTools} className="btn btn-sm lg:hidden" aria-label="工具">
        <PouchIcon size={18} />
        <span className="hidden sm:inline">工具</span>
      </button>
      <AccountMenu user={user} {...menu} />
    </header>
  );
}
