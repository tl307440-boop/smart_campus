"use client";

import { useState, type FormEvent, type KeyboardEvent } from "react";
import { StopIcon } from "@/components/SketchIcons";

interface Props {
  disabled: boolean;
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

export function Composer({ disabled, busy, onSend, onStop }: Props) {
  const [value, setValue] = useState("");

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (busy || disabled) return;
    const text = value.trim();
    if (!text) return;
    onSend(text);
    setValue("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter 发送，Shift+Enter 换行；输入法选词时的 Enter 不算
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form onSubmit={submit} className="flex items-end gap-3" data-pet-avoid>
      <textarea
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
        <button type="button" onClick={onStop} className="btn">
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
