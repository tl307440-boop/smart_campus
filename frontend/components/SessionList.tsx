"use client";

import { useEffect, useRef, useState } from "react";
import { CloseIcon, NewPageIcon, TrashIcon } from "@/components/SketchIcons";
import { useToast } from "@/components/Toast";
import { errorDetail, type SessionSummary } from "@/lib/api";

const DAY_MS = 24 * 60 * 60 * 1000;
const WEEK_DAYS = 7;

interface Props {
  open: boolean;
  sessions: SessionSummary[] | null;
  error: string | null;
  currentId: string | null;
  onClose: () => void;
  onOpen: (threadId: string) => void;
  onNew: () => void;
  onDelete: (threadId: string) => Promise<void>;
}

function startOfToday(): number {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
}

function groupLabel(updatedAt: string, today: number): string {
  const time = new Date(updatedAt).getTime();
  if (time >= today) return "今天";
  if (time >= today - DAY_MS) return "昨天";
  if (time >= today - WEEK_DAYS * DAY_MS) return "7 天内";
  return "更早";
}

function timeLabel(updatedAt: string, today: number): string {
  const date = new Date(updatedAt);
  if (date.getTime() >= today) return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function grouped(sessions: SessionSummary[]): [string, SessionSummary[]][] {
  const today = startOfToday();
  const groups = new Map<string, SessionSummary[]>();
  for (const session of sessions) {
    const label = groupLabel(session.updated_at, today);
    groups.set(label, [...(groups.get(label) ?? []), session]);
  }
  return [...groups.entries()];
}

function SessionRow({ session, current, onOpen, onDelete }: {
  session: SessionSummary;
  current: boolean;
  onOpen: () => void;
  onDelete: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const toast = useToast();

  async function remove() {
    setDeleting(true);
    try {
      await onDelete();
      toast.success("已删除这段对话");
    } catch (err) {
      toast.error(`没删掉：${errorDetail(err)}`);
      setDeleting(false);
      setConfirming(false);
    }
  }

  return (
    <li className="group relative flex items-center gap-1 rounded-xl hover:bg-skin/60">
      <button
        type="button"
        onClick={onOpen}
        aria-current={current ? "page" : undefined}
        className="min-w-0 flex-1 px-3 py-2 text-left"
      >
        <span className={`block truncate text-[0.9375rem] ${current ? "marker" : ""}`}>{session.title}</span>
        <span className="text-[0.8125rem] text-muted tabular-nums">{timeLabel(session.updated_at, startOfToday())}</span>
      </button>
      {confirming ? (
        <span className="flex shrink-0 items-center gap-1 pr-2">
          <button type="button" disabled={deleting} onClick={remove} className="btn btn-sm btn-danger">
            {deleting ? "删除中…" : "删掉"}
          </button>
          <button type="button" disabled={deleting} onClick={() => setConfirming(false)} className="btn btn-sm">
            算了
          </button>
        </span>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          aria-label={`删除「${session.title}」`}
          className="mr-1.5 grid size-8 shrink-0 place-items-center rounded-full text-muted hover:text-danger focus-visible:opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100"
        >
          <TrashIcon size={17} />
        </button>
      )}
    </li>
  );
}

/** 左边翻出来的历史对话：换浏览器登录也在，因为它存在学校的数据库里。 */
export function SessionList({ open, sessions, error, currentId, onClose, onOpen, onNew, onDelete }: Props) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      <div
        aria-hidden="true"
        onClick={onClose}
        className={`fixed inset-0 z-40 bg-ink/25 transition-opacity duration-200 ${open ? "opacity-100" : "pointer-events-none opacity-0"}`}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="历史对话"
        tabIndex={-1}
        inert={!open}
        className={`sketch-lg fixed inset-y-0 left-0 z-50 flex w-full flex-col bg-paper outline-none sm:w-[22rem] lg:inset-y-4 lg:left-4 ${
          open
            ? "translate-x-0 opacity-100 transition-[translate,opacity] duration-[240ms] ease-[cubic-bezier(0.2,0.8,0.3,1)]"
            : "-translate-x-10 opacity-0 transition-[translate,opacity] duration-[160ms] ease-in"
        }`}
      >
        <div className="flex items-center gap-2 border-b-2 border-ink px-4 py-2.5">
          <h2 className="flex-1 font-display text-xl">历史对话</h2>
          <button type="button" onClick={onNew} className="btn btn-sm">
            <NewPageIcon size={18} />
            新的一页
          </button>
          <button type="button" onClick={onClose} className="btn btn-icon btn-sm" aria-label="关上历史对话">
            <CloseIcon size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-3">
          {error && <p className="px-3 text-[0.9375rem] text-danger">{errorDetail(error)}</p>}
          {!error && sessions === null && <p className="px-3 text-muted">正在翻…</p>}
          {sessions?.length === 0 && (
            <p className="px-3 text-[0.9375rem] text-muted">还没有聊过。问的问题会留在这里，换个浏览器登录也能看到。</p>
          )}
          {sessions &&
            grouped(sessions).map(([label, items]) => (
              <section key={label} className="mb-3">
                <p className="px-3 pb-1 text-[0.8125rem] text-muted">{label}</p>
                <ul className="space-y-0.5">
                  {items.map((session) => (
                    <SessionRow
                      key={session.thread_id}
                      session={session}
                      current={session.thread_id === currentId}
                      onOpen={() => onOpen(session.thread_id)}
                      onDelete={() => onDelete(session.thread_id)}
                    />
                  ))}
                </ul>
              </section>
            ))}
        </div>
      </div>
    </>
  );
}
