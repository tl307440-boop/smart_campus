"use client";

import { useState, type FormEvent, type KeyboardEvent } from "react";

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
    // Enter 发送，Shift+Enter 换行
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form
      onSubmit={submit}
      className="flex items-end gap-2 rounded-2xl border border-line bg-surface p-2"
    >
      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        rows={1}
        placeholder="请输入你的问题，Enter 发送，Shift+Enter 换行"
        className="max-h-40 min-h-11 flex-1 resize-y bg-transparent px-3 py-2.5 text-[0.95rem] text-parchment outline-none placeholder:text-muted disabled:opacity-50"
      />

      {busy ? (
        <button
          type="button"
          onClick={onStop}
          className="h-11 shrink-0 rounded-full border border-line px-5 text-sm text-parchment transition hover:border-line-strong hover:bg-gold-soft"
        >
          停止
        </button>
      ) : (
        <button
          type="submit"
          disabled={disabled || !value.trim()}
          className="h-11 shrink-0 rounded-full bg-linear-to-br from-[#c8954a] to-gold px-6 text-sm font-semibold text-[#1a1208] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          发送
        </button>
      )}
    </form>
  );
}
