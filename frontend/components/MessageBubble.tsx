import type { ChatMessage, ChatSource } from "@/lib/api";

interface Props {
  message: ChatMessage;
  streaming?: boolean;
  assistantName?: string;
}

function scoreLabel(source: ChatSource): string | null {
  if (source.score === null) return null;
  return `相似度 ${Math.round(source.score * 100)}%`;
}

function SourceCards({ sources }: { sources: ChatSource[] }) {
  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs text-muted">参考来源</p>
      {sources.map((source) => (
        <details
          key={`${source.index}-${source.source}`}
          className="rounded-lg border border-line bg-ink-elevated px-3 py-2"
        >
          <summary className="flex cursor-pointer items-center gap-2 text-xs text-parchment">
            <span className="rounded bg-gold-soft px-1.5 text-gold">{source.index}</span>
            <span className="min-w-0 flex-1 truncate" title={source.source}>
              {source.source}
            </span>
            {scoreLabel(source) && <span className="shrink-0 text-muted">{scoreLabel(source)}</span>}
          </summary>
          {source.snippet ? (
            <p className="mt-2 whitespace-pre-wrap text-xs text-muted">{source.snippet}…</p>
          ) : (
            <p className="mt-2 text-xs text-muted">整篇精读，未截取片段。</p>
          )}
        </details>
      ))}
    </div>
  );
}

export function MessageBubble({ message, streaming = false, assistantName = "小懒" }: Props) {
  const isUser = message.role === "user";
  const sources = message.sources ?? [];

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] animate-fade-in rounded-2xl border border-line px-4 py-3 leading-relaxed ${
          isUser ? "bg-bubble-user" : "bg-bubble-assistant"
        }`}
      >
        {!isUser && (
          <p className="mb-1 font-display text-xs tracking-widest text-gold">{assistantName}</p>
        )}

        <p className="whitespace-pre-wrap break-words text-[0.95rem] text-parchment">
          {message.content}
          {streaming && <span className="ml-0.5 animate-pulse text-gold">▌</span>}
        </p>

        {sources.length > 0 && <SourceCards sources={sources} />}

        {sources.length === 0 && message.context && (
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
