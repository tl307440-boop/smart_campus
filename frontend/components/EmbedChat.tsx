"use client";

import { useRef } from "react";
import { ChatLog } from "@/components/ChatLog";
import { Composer } from "@/components/Composer";
import { PetPortrait } from "@/components/PetPortrait";
import { SuggestedQuestions } from "@/components/SuggestedQuestions";
import { useChat } from "@/hooks/useChat";
import { useStickToBottom } from "@/hooks/useStickToBottom";

interface Props {
  assistantName: string;
  tenantName: string;
}

const EMBED_NOTE = { embedded: true } as const;

/** 挂件里的一页小草稿纸：没有工具抽屉、记忆和设置，只有问答。 */
export function EmbedChat({ assistantName, tenantName }: Props) {
  const { messages, pending, progress, error, restoring, busy, threadId, send, stop, regenerate, editLast } = useChat(
    undefined,
    { embedded: true },
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  useStickToBottom(scrollRef, `${messages.length}:${pending.length}:${progress?.phase}`);

  return (
    <div className="flex h-dvh flex-col bg-paper">
      <header className="flex items-center gap-2.5 border-b-2 border-ink px-4 py-2">
        <PetPortrait eyesClosed={false} className="size-10 shrink-0" />
        <div className="min-w-0">
          <p className="truncate font-display text-lg leading-tight">{assistantName}</p>
          <p className="truncate text-[0.8125rem] text-muted">{tenantName || "智悦校园"}</p>
        </div>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-5">
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
              <ChatLog
                messages={messages}
                pending={pending}
                progress={progress}
                error={error}
                busy={busy}
                assistantName={assistantName}
                noteProps={EMBED_NOTE}
                onRegenerate={regenerate}
                onEdit={editLast}
              />
            </>
          )}
        </div>
      </div>

      <div className="border-t-2 border-ink px-4 py-3">
        <Composer disabled={restoring || !threadId} busy={busy} onSend={send} onStop={stop} />
      </div>
    </div>
  );
}
