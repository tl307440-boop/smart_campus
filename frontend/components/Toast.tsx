"use client";

import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";
import { CheckIcon, CloseIcon } from "@/components/SketchIcons";

/** 成功的提示几秒后自己消失；失败的要用户亲手关掉，免得没看清原因就没了；上课提醒留得久一点，但不用手动关 */
const DISMISS_MS: Partial<Record<ToastKind, number>> = { success: 3000, notice: 15000 };
const MAX_TOASTS = 3;

type ToastKind = "success" | "error" | "notice";

interface ToastItem {
  id: number;
  kind: ToastKind;
  text: string;
}

interface ToastApi {
  success: (text: string) => void;
  error: (text: string) => void;
  /** 需要看一眼但不是好事也不是坏事，比如马上要上课了 */
  notice: (text: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id: number) => setItems((list) => list.filter((item) => item.id !== id)), []);

  const push = useCallback(
    (kind: ToastKind, text: string) => {
      const id = ++nextId.current;
      setItems((list) => [...list.filter((item) => item.text !== text), { id, kind, text }].slice(-MAX_TOASTS));
      const lifetime = DISMISS_MS[kind];
      if (lifetime) window.setTimeout(() => dismiss(id), lifetime);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({ success: (text) => push("success", text), error: (text) => push("error", text), notice: (text) => push("notice", text) }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed top-4 right-4 left-4 z-[60] flex flex-col items-end gap-2 sm:left-auto sm:w-80"
      >
        {items.map((item, i) => (
          <div
            key={item.id}
            role={item.kind === "error" ? "alert" : "status"}
            className="sketch pop-in pointer-events-auto flex w-full items-start gap-2.5 bg-paper px-3.5 py-3 shadow-[3px_4px_0_rgba(27,18,20,0.12)]"
            style={{ rotate: `${i % 2 ? 0.6 : -0.5}deg` }}
          >
            <span
              className={`mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border-[1.5px] border-ink font-display text-sm ${
                item.kind === "success" ? "bg-flower" : item.kind === "notice" ? "bg-pollen" : "bg-skin text-pink"
              }`}
              aria-hidden="true"
            >
              {item.kind === "success" ? <CheckIcon size={14} /> : item.kind === "notice" ? "课" : "!"}
            </span>
            <p className="min-w-0 flex-1 text-[0.9375rem] leading-snug break-words">{item.text}</p>
            {item.kind !== "success" && (
              <button type="button" onClick={() => dismiss(item.id)} className="shrink-0 text-muted hover:text-ink" aria-label="关掉这条提示">
                <CloseIcon size={16} />
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast 必须包在 ToastProvider 内");
  return ctx;
}
