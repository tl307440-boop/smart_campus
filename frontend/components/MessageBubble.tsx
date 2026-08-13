import type { ChatMessage } from "@/lib/api";

interface Props {
  message: ChatMessage;
  streaming?: boolean;
}

export function MessageBubble({ message, streaming = false }: Props) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] animate-fade-in rounded-2xl border border-line px-4 py-3 leading-relaxed ${
          isUser ? "bg-bubble-user" : "bg-bubble-assistant"
        }`}
      >
        {!isUser && (
          <p className="mb-1 font-display text-xs tracking-widest text-gold">小懒</p>
        )}

        <p className="whitespace-pre-wrap break-words text-[0.95rem] text-parchment">
          {message.content}
          {streaming && <span className="ml-0.5 animate-pulse text-gold">▌</span>}
        </p>

        {message.context && (
          <details className="mt-3 rounded-lg border border-line bg-ink-elevated px-3 py-2">
            <summary className="cursor-pointer text-xs text-muted">
              查看知识库来源片段
            </summary>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-muted">
              {message.context}
            </pre>
          </details>
        )}

        {message.emotion && (
          <details className="mt-2 rounded-lg border border-line bg-ink-elevated px-3 py-2">
            <summary className="cursor-pointer text-xs text-muted">情感分析结果</summary>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-muted">
              {JSON.stringify(message.emotion, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}
