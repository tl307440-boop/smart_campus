"use client";

import { useEffect, useRef } from "react";
import { Composer } from "@/components/Composer";
import { ErrorNote } from "@/components/ErrorNote";
import { MessageBubble } from "@/components/MessageBubble";
import { PetPortrait } from "@/components/PetPortrait";
import { SuggestedQuestions } from "@/components/SuggestedQuestions";
import { useChat } from "@/hooks/useChat";
import { isFallbackAnswer, mayBeFallback } from "@/lib/friendly-error";

interface Props {
  assistantName: string;
  tenantName: string;
}

/** 挂件里的一页小草稿纸：没有工具抽屉、记忆和设置，只有问答。 */
export function EmbedChat({ assistantName, tenantName }: Props) {
  const { messages, pending, status, error, restoring, busy, threadId, send, stop } = useChat(
    undefined,
    { embedded: true },
  );
  const last = messages.at(-1);
  const answerFailed = last?.role === "assistant" && isFallbackAnswer(last.content);
  const signedId = !busy && last?.role === "assistant" && !answerFailed ? last.id : null;
  const pendingShown = pending && !mayBeFallback(pending) ? pending : "";

  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending, status]);

  return (
    <div className="flex h-dvh flex-col bg-paper">
      <header className="flex items-center gap-2.5 border-b-2 border-ink px-4 py-2">
        <PetPortrait eyesClosed={false} className="size-10 shrink-0" />
        <div className="min-w-0">
          <p className="truncate font-display text-lg leading-tight">{assistantName}</p>
          <p className="truncate text-[0.8125rem] text-muted">{tenantName || "智悦校园"}</p>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-5">
        <div className="flex flex-col gap-6">
          {restoring ? (
            <p className="text-center text-muted">正在连接…</p>
          ) : (
            <>
              {messages.length === 0 && (
                <div className="rise-in space-y-4">
                  <p>
                    你好！我是{assistantName}{tenantName ? `，${tenantName}的校园助手` : ""}。规章制度、奖助学金这些都可以问我。
                  </p>
                  <SuggestedQuestions disabled={busy || !threadId} onAsk={(q) => void send(q)} limit={3} />
                </div>
              )}
              {messages.map((message) =>
                message.role === "assistant" && isFallbackAnswer(message.content) ? (
                  <ErrorNote key={message.id} raw={message.content} embedded />
                ) : (
                  <MessageBubble key={message.id} message={message} signed={message.id === signedId} />
                ),
              )}
              {pendingShown && (
                <MessageBubble message={{ id: "pending", role: "assistant", content: pendingShown }} streaming />
              )}
              {status && !pendingShown && <p className="text-[0.9375rem] text-muted" role="status">{status}</p>}
            </>
          )}
          {error && !answerFailed && <ErrorNote raw={error} embedded />}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="border-t-2 border-ink px-4 py-3">
        <Composer disabled={restoring || !threadId} busy={busy} onSend={send} onStop={stop} />
      </div>
    </div>
  );
}
