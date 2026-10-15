"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { StopIcon } from "@/components/SketchIcons";

/** 外面塞进输入框的一段话（「就这段再问」）；n 变了才算新的一次 */
export interface ComposerPrefill {
  text: string;
  n: number;
}

interface Props {
  disabled: boolean;
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  prefill?: ComposerPrefill;
  /** Ctrl/⌘ + ↑ 填回上一句，和终端的习惯一样 */
  lastQuestion?: string;
}

export function Composer({ disabled, busy, onSend, onStop, prefill, lastQuestion }: Props) {
  const [value, setValue] = useState("");
  const [seenPrefill, setSeenPrefill] = useState(prefill?.n);
  const fieldRef = useRef<HTMLTextAreaElement>(null);

  if (prefill && prefill.n !== seenPrefill) {
    setSeenPrefill(prefill.n);
    setValue((current) => `${prefill.text}${current}`);
  }

  useEffect(() => {
    if (!prefill?.n) return;
    const field = fieldRef.current;
    field?.focus();
    field?.setSelectionRange(field.value.length, field.value.length);
  }, [prefill?.n]);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (busy || disabled) return;
    const text = value.trim();
    if (!text) return;
    onSend(text);
    setValue("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) return;
    // Enter 发送，Shift+Enter 换行；输入法选词时的 Enter 不算
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    } else if (event.key === "Escape" && busy) {
      event.preventDefault();
      onStop();
    } else if (event.key === "ArrowUp" && (event.ctrlKey || event.metaKey) && lastQuestion && !value) {
      event.preventDefault();
      setValue(lastQuestion);
    }
  }

  return (
    <form onSubmit={submit} className="flex items-end gap-3" data-pet-avoid>
      <textarea
        ref={fieldRef}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        rows={1}
        aria-label="你的问题"
        placeholder="写下你的问题，Enter 发送"
        className="field field-sizing-content max-h-40 min-h-11 flex-1 resize-none py-2.5 disabled:opacity-50"
      />

      {busy ? (
        <button type="button" onClick={onStop} className="btn" title="停下（Esc）">
          <StopIcon size={18} />
          停下
        </button>
      ) : (
        <button type="submit" disabled={disabled || !value.trim()} className="btn btn-primary px-5">
          发送
        </button>
      )}
    </form>
  );
}
