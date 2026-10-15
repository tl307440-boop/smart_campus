"use client";

import { ErrorNote } from "@/components/ErrorNote";
import { MessageBubble } from "@/components/MessageBubble";
import { PetPortrait } from "@/components/PetPortrait";
import type { ChatMessage } from "@/lib/api";
import { type ChatProgress, progressText } from "@/lib/chat-progress";
import { describeFailure, isFallbackAnswer, mayBeFallback } from "@/lib/friendly-error";

interface NoteProps {
  embedded?: boolean;
  canDebug?: boolean;
  canConfigure?: boolean;
  onOpenService?: () => void;
}

interface Props {
  messages: ChatMessage[];
  pending: string;
  progress: ChatProgress | null;
  error: string | null;
  busy: boolean;
  assistantName: string;
  showDebug?: boolean;
  noteProps: NoteProps;
  onRegenerate: () => void;
  onEdit: (text: string) => void;
}

interface Row {
  message: ChatMessage;
  /** 同一个问题连续失败的次数，只在报错那一行上大于 1 */
  repeat: number;
}

function failedAnswer(message: ChatMessage | undefined): boolean {
  return message?.role === "assistant" && isFallbackAnswer(message.content);
}

/** 同一句话连着问、连着以同一种原因失败：只留一组「提问 + 报错 ×N」，不堆一串一模一样的卡片 */
function collapseRepeatedFailures(messages: ChatMessage[]): Row[] {
  const rows: Row[] = [];
  for (let i = 0; i < messages.length; i++) {
    const message = messages[i];
    const answer = messages[i + 1];
    const [prevQuestion, prevAnswer] = [rows.at(-2), rows.at(-1)];
    if (
      message.role === "user" &&
      answer &&
      failedAnswer(answer) &&
      prevQuestion?.message.role === "user" &&
      prevQuestion.message.content === message.content &&
      prevAnswer &&
      failedAnswer(prevAnswer.message) &&
      describeFailure(prevAnswer.message.content).kind === describeFailure(answer.content).kind
    ) {
      prevAnswer.repeat += 1;
      prevAnswer.message = answer;
      i++;
      continue;
    }
    rows.push({ message, repeat: 1 });
  }
  return rows;
}

function WaitingNote({ text }: { text: string }) {
  return (
    <div className="animate-fade-in flex items-center gap-3" role="status">
      <PetPortrait eyesClosed={false} className="pencil-bob size-10 shrink-0 [animation-duration:1.4s]" />
      <p className="flex items-center gap-2 text-[0.9375rem] text-muted">
        {text}
        <span className="flex gap-1" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <i key={i} className="pencil-bob size-1.5 rounded-full border border-ink bg-pollen" style={{ animationDelay: `${i * 120}ms` }} />
          ))}
        </span>
      </p>
    </div>
  );
}

/** 对话正文：消息、正在写的回答、等待提示和最后的报错。主页面和嵌入挂件共用。 */
export function ChatLog({
  messages,
  pending,
  progress,
  error,
  busy,
  assistantName,
  showDebug = false,
  noteProps,
  onRegenerate,
  onEdit,
}: Props) {
  const last = messages.at(-1);
  const answerFailed = failedAnswer(last);
  const signedId = !busy && last?.role === "assistant" && !answerFailed ? last.id : null;
  const lastUserId = messages.findLast((message) => message.role === "user")?.id;
  // 兜底回答也会被流式吐出来，先不显示，避免原始报错闪一下
  const pendingShown = pending && !mayBeFallback(pending) ? pending : "";
  const retry = busy ? undefined : onRegenerate;

  return (
    <>
      {collapseRepeatedFailures(messages).map(({ message, repeat }) => {
        const latest = !busy && (message.id === lastUserId || message.id === last?.id);
        return failedAnswer(message) ? (
          <ErrorNote
            key={message.id}
            raw={message.content}
            safety={message.safety}
            repeat={repeat}
            onRetry={message.id === last?.id ? retry : undefined}
            {...noteProps}
          />
        ) : (
          <MessageBubble
            key={message.id}
            message={message}
            signed={message.id === signedId}
            showDebug={showDebug}
            latest={latest}
            onRegenerate={onRegenerate}
            onEdit={onEdit}
          />
        );
      })}

      {pendingShown && <MessageBubble message={{ id: "pending", role: "assistant", content: pendingShown }} streaming />}

      {progress && !pendingShown && <WaitingNote text={progressText(progress, assistantName)} />}

      {error && !answerFailed && <ErrorNote raw={error} onRetry={last?.role === "user" ? retry : undefined} {...noteProps} />}
    </>
  );
}
