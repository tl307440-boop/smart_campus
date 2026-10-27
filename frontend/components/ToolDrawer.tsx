"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";
import { CloseIcon } from "@/components/SketchIcons";

export type ToolTab = "service" | "memory" | "jw" | "knowledge" | "workspace" | "embeds" | "usage";

export const TAB_LABEL: Record<ToolTab, string> = {
  service: "AI 服务",
  memory: "记忆",
  jw: "教务账号",
  knowledge: "知识库",
  workspace: "工作区",
  embeds: "挂件",
  usage: "使用情况",
};

/** 每一页抽屉的标题和一句说明 */
export function PaneTitle({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mb-5">
      <h2 className="font-display text-2xl leading-tight">{title}</h2>
      <p className="mt-1 text-[0.9375rem] text-muted">{children}</p>
    </div>
  );
}

interface Props {
  tabs: ToolTab[];
  /** 当前（或刚关上的）那一页，关的时候内容留着，抽屉折回去才不会先变空 */
  tab: ToolTab;
  open: boolean;
  onOpen: (tab: ToolTab) => void;
  onClose: () => void;
  children: ReactNode;
}

/**
 * 纸页右边缘的索引标签（宽屏）+ 从标签处翻出的抽屉。窄屏没有页边，由页头的「工具」按钮打开。
 * 抽屉常驻 DOM，开合只切 data-open，开和关互为逆过程。
 */
export function ToolDrawer({ tabs, tab, open, onOpen, onClose, children }: Props) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [originY, setOriginY] = useState(120);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  function openFromEdge(next: ToolTab, target: HTMLElement) {
    const rect = target.getBoundingClientRect();
    setOriginY(rect.top + rect.height / 2);
    onOpen(next);
  }

  return (
    <>
      <nav
        aria-label="工具"
        className={`fixed top-28 left-[calc(50%+24rem-2px)] z-20 hidden flex-col gap-2 transition-opacity duration-200 lg:flex ${
          open ? "pointer-events-none opacity-0" : ""
        }`}
      >
        {tabs.map((item, i) => (
          <button
            key={item}
            type="button"
            onClick={(event) => openFromEdge(item, event.currentTarget)}
            className="sketch rounded-l-none bg-skin px-2 py-3 font-display text-base leading-none transition-transform duration-150 [writing-mode:vertical-rl] hover:translate-x-1"
            style={{ rotate: `${i % 2 ? 1.2 : -1}deg` }}
          >
            {TAB_LABEL[item]}
          </button>
        ))}
      </nav>

      <div
        aria-hidden="true"
        onClick={onClose}
        className={`fixed inset-0 z-40 bg-ink/25 transition-opacity duration-200 ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={TAB_LABEL[tab]}
        tabIndex={-1}
        inert={!open}
        style={{ transformOrigin: `100% ${originY}px` }}
        className={`sketch-lg fixed inset-y-0 right-0 z-50 flex w-full flex-col bg-paper outline-none sm:w-[32rem] lg:inset-y-4 lg:right-4 ${
          open
            ? "translate-x-0 scale-100 opacity-100 transition-[translate,scale,opacity] duration-[260ms] ease-[cubic-bezier(0.2,0.8,0.3,1)]"
            : "translate-x-10 scale-x-[0.94] opacity-0 transition-[translate,scale,opacity] duration-[170ms] ease-in"
        }`}
      >
        <div className="flex items-center gap-2 border-b-2 border-ink px-4 pt-3 pb-2.5 sm:px-6">
          <div role="tablist" aria-label="工具分页" className="flex min-w-0 flex-1 gap-1 overflow-x-auto p-1">
            {tabs.map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={item === tab}
                onClick={() => onOpen(item)}
                className={`relative shrink-0 rounded-full px-3 py-1.5 font-display text-base leading-none ${
                  item === tab ? "text-ink" : "text-muted hover:text-ink"
                }`}
              >
                {item === tab && (
                  <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="absolute -top-1 -left-1 h-[calc(100%+0.5rem)] w-[calc(100%+0.5rem)]" aria-hidden="true">
                    <path d="M8 22C6 9 30 3 55 4s40 6 38 17-30 15-52 14S9 31 12 18" fill="none" stroke="var(--color-pink)" strokeWidth={2.4} vectorEffect="non-scaling-stroke" strokeLinecap="round" />
                  </svg>
                )}
                {TAB_LABEL[item]}
              </button>
            ))}
          </div>
          <button type="button" onClick={onClose} className="btn btn-icon btn-sm" aria-label="关上抽屉">
            <CloseIcon size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">{children}</div>
      </div>
    </>
  );
}
