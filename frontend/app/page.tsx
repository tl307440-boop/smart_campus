"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Composer } from "@/components/Composer";
import { ConsolePanel } from "@/components/ConsolePanel";
import { MessageBubble } from "@/components/MessageBubble";
import { useChat } from "@/hooks/useChat";

const WELCOME = "同学，你好！我是你的校园助手小懒。请问有什么可以帮你？";

export default function ChatPage() {
  const [ttsEnabled, setTtsEnabled] = useState(false);

  const speak = useCallback(
    (text: string) => {
      if (!ttsEnabled) return;
      const synth = window.speechSynthesis;
      if (!synth) return;
      synth.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "zh-CN";
      synth.speak(utterance);
    },
    [ttsEnabled],
  );

  const { messages, pending, status, error, restoring, busy, threadId, send, stop, startNewSession } =
    useChat(speak);

  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending, status]);

  function handleToggleTts(enabled: boolean) {
    setTtsEnabled(enabled);
    if (!enabled) window.speechSynthesis?.cancel();
  }

  function handleNewSession() {
    window.speechSynthesis?.cancel();
    void startNewSession();
  }

  return (
    <div className="flex h-dvh">
      <ConsolePanel
        threadId={threadId}
        ttsEnabled={ttsEnabled}
        onToggleTts={handleToggleTts}
        onNewSession={handleNewSession}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="border-b border-line px-6 py-5 text-center">
          <h1 className="font-display text-2xl font-bold tracking-[0.08em]">
            智悦校园 · <span className="text-gold">小懒</span>
          </h1>
          <p className="mt-1 text-sm text-muted">
            校园问答助手。问规章、查资助、解疑惑——像在灯下和同学聊聊。
          </p>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            {restoring ? (
              <p className="text-center text-sm text-muted">正在恢复会话…</p>
            ) : (
              <>
                {messages.length === 0 && (
                  <MessageBubble
                    message={{ id: "welcome", role: "assistant", content: WELCOME }}
                  />
                )}

                {messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}

                {pending && (
                  <MessageBubble
                    message={{ id: "pending", role: "assistant", content: pending }}
                    streaming
                  />
                )}

                {status && !pending && (
                  <p className="animate-fade-in text-sm text-muted">{status}</p>
                )}
              </>
            )}

            {error && (
              <p className="rounded-xl border border-danger/40 bg-surface px-4 py-3 text-sm text-danger">
                {error}
              </p>
            )}

            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-line px-6 py-4">
          <div className="mx-auto max-w-3xl">
            <Composer disabled={restoring || !threadId} busy={busy} onSend={send} onStop={stop} />
          </div>
        </div>
      </main>
    </div>
  );
}
