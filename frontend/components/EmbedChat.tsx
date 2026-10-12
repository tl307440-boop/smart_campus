"use client";

import { useEffect, useRef } from "react";
import { Composer } from "@/components/Composer";
import { MessageBubble } from "@/components/MessageBubble";
import { useChat } from "@/hooks/useChat";

interface Props {
  assistantName: string;
  tenantName: string;
}

/** 挂件里的精简聊天界面：没有控制台、记忆和设置，只有问答。 */
export function EmbedChat({ assistantName, tenantName }: Props) {
  const { messages, pending, status, error, restoring, busy, threadId, send, stop } = useChat(
    undefined,
    { embedded: true },
  );

  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending, status]);

  const welcome = `你好！我是${tenantName ? `${tenantName}的` : ""}校园助手${assistantName}，可以问我规章制度、奖助学金等问题。`;

  return (
    <div className="flex h-dvh flex-col">
      <header className="border-b border-line px-4 py-3">
        <p className="font-display text-base font-bold text-parchment">
          {tenantName || "智悦校园"} · <span className="text-gold">{assistantName}</span>
        </p>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        <div className="flex flex-col gap-3">
          {restoring ? (
            <p className="text-center text-sm text-muted">正在连接…</p>
          ) : (
            <>
              {messages.length === 0 && (
                <MessageBubble
                  message={{ id: "welcome", role: "assistant", content: welcome }}
                  assistantName={assistantName}
                />
              )}
              {messages.map((message) => (
                <MessageBubble key={message.id} message={message} assistantName={assistantName} />
              ))}
              {pending && (
                <MessageBubble
                  message={{ id: "pending", role: "assistant", content: pending }}
                  assistantName={assistantName}
                  streaming
                />
              )}
              {status && !pending && <p className="text-sm text-muted">{status}</p>}
            </>
          )}
          {error && (
            <p className="rounded-xl border border-danger/40 bg-surface px-3 py-2 text-sm text-danger">
              {error.includes("401") ? "会话已过期，请刷新页面后重试" : error}
            </p>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="border-t border-line px-3 py-3">
        <Composer disabled={restoring || !threadId} busy={busy} onSend={send} onStop={stop} />
      </div>
    </div>
  );
}
