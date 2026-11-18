"use client";

import { useEffect, useRef, useState } from "react";
import { HistoryIcon, NewPageIcon } from "@/components/SketchIcons";
import type { AuthUser } from "@/lib/auth/session";

export type CoreView = "chat" | "archive" | "connect";

interface Props {
  assistantName: string;
  user: AuthUser | null;
  isStaff: boolean;
  threadId: string | null;
  activeView: CoreView;
  pendingTodoCount?: number;
  nightMode: boolean;
  onChangeView: (view: CoreView) => void;
  onToggleNightMode: () => void;
  onNewSession: () => void;
  onOpenHistory?: () => void;
  onLogout: () => void;
}

function AccountMenu({
  user,
  isStaff,
  threadId,
  onLogout,
}: Pick<Props, "user" | "isStaff" | "threadId" | "onLogout">) {
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
          {isStaff && threadId && (
            <p className="mt-3 border-t border-dashed border-rule pt-3 text-[0.8125rem] break-all text-muted">
              会话 {threadId}
            </p>
          )}
          <button type="button" onClick={onLogout} className="link-ink mt-3 text-[0.9375rem]">
            退出登录
          </button>
        </div>
      )}
    </div>
  );
}

/** 精简 3 视图页头：窄屏专注主对话，桌面端内置 3 核心切换与深夜树洞按钮 */
export function ChatHeader({
  assistantName,
  user,
  activeView,
  pendingTodoCount = 0,
  nightMode,
  onChangeView,
  onToggleNightMode,
  onNewSession,
  onOpenHistory,
  ...menu
}: Props) {
  const navItems: { id: CoreView; label: string; badge?: number }[] = [
    { id: "chat", label: "伴侣对话" },
    { id: "archive", label: "情绪与记忆", badge: pendingTodoCount || undefined },
    { id: "connect", label: "连接设置" },
  ];

  return (
    <header className="flex items-center gap-2 border-b-2 border-ink px-3 py-2.5 sm:px-5">
      {onOpenHistory && (
        <button
          type="button"
          onClick={onOpenHistory}
          className="btn btn-icon btn-sm"
          aria-label="历史对话"
          title="历史对话"
        >
          <HistoryIcon size={18} />
        </button>
      )}
      <div className="min-w-0 flex-1 md:flex-none md:pr-2">
        <p className="truncate font-display text-xl leading-tight sm:text-2xl">{assistantName}</p>
        {user && <p className="truncate text-[0.75rem] text-muted">{user.tenantName} · 智悦伴侣</p>}
      </div>

      {/* 桌面端 3 核心视图切换 */}
      <nav aria-label="核心视图" className="hidden flex-1 items-center justify-center gap-1.5 md:flex">
        {navItems.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onChangeView(item.id)}
            className={`btn btn-sm ${activeView === item.id ? "btn-primary" : ""}`}
          >
            <span>{item.label}</span>
            {item.badge ? (
              <span className="rounded-full bg-ink px-1.5 py-0.2 text-[0.7rem] text-paper tabular-nums">
                {item.badge}
              </span>
            ) : null}
          </button>
        ))}
      </nav>

      <button
        type="button"
        onClick={onToggleNightMode}
        className="btn btn-sm"
        aria-label={nightMode ? "切回日间纸面" : "进入深夜树洞模式"}
        title={nightMode ? "切回日间纸面" : "进入深夜树洞模式"}
      >
        <span>{nightMode ? "☀️" : "🌙"}</span>
        <span className="hidden sm:inline">{nightMode ? "日间" : "树洞"}</span>
      </button>

      {activeView === "chat" && (
        <button type="button" onClick={onNewSession} className="btn btn-sm" aria-label="新的一页">
          <NewPageIcon size={18} />
          <span className="hidden sm:inline">新的一页</span>
        </button>
      )}

      <AccountMenu user={user} {...menu} />
    </header>
  );
}
