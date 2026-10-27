"use client";

import { ErrorNote } from "@/components/ErrorNote";
import { MessageBubble } from "@/components/MessageBubble";
import { WaitingNote } from "@/components/WaitingNote";
import type { ChatMessage, FeedbackRating, FeedbackReason } from "@/lib/api";
import type { ChatProgress } from "@/lib/chat-progress";
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
  /** 点赞点踩；挂件里的访客对话不入库，不传 */
  onRate?: (id: string, rating: FeedbackRating, reason?: FeedbackReason) => Promise<void>;
  /** 点一下「接着问」的按钮就把这句话发出去；不传就不显示 */
  onAsk?: (question: string) => void;
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
  onRate,
  onAsk,
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
            onRate={onRate}
          />
        );
      })}

      {signedId && onAsk && last?.followUps && last.followUps.length > 0 && (
        <ul className="rise-in flex flex-wrap gap-2" aria-label="接着问">
          {last.followUps.map((question) => (
            <li key={question}>
              <button type="button" onClick={() => onAsk(question)} className="btn btn-sm">
                {question}
              </button>
            </li>
          ))}
        </ul>
      )}

      {pendingShown && <MessageBubble message={{ id: "pending", role: "assistant", content: pendingShown }} streaming />}

      {progress && !pendingShown && <WaitingNote progress={progress} name={assistantName} />}

      {error && !answerFailed && <ErrorNote raw={error} onRetry={last?.role === "user" ? retry : undefined} {...noteProps} />}
    </>
  );
}
