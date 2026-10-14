import { Flower } from "@/components/DeskPet";
import { SafetyCard } from "@/components/SafetyCard";
import { PaperclipIcon, PencilIcon } from "@/components/SketchIcons";
import type { ChatMessage, ChatSource } from "@/lib/api";

interface Props {
  message: ChatMessage;
  streaming?: boolean;
  /** 最新一条答完的回答：末尾画一朵蓝花 */
  signed?: boolean;
  /** 管理员排查用：情感分析等调试信息 */
  showDebug?: boolean;
}

function scoreLabel(source: ChatSource): string | null {
  if (source.score === null) return null;
  return `相似度 ${Math.round(source.score * 100)}%`;
}

/** wiki/concepts/国家助学金.md → 国家助学金 */
function sourceTitle(path: string): string {
  const base = path.split(/[\\/]/).pop() ?? path;
  return base.replace(/\.(md|txt|pdf|docx)$/i, "") || path;
}

function Slip({ index, title, full, score, tilt = 0, children }: {
  index: number | string; title: string; full: string; score?: string | null; tilt?: number; children: React.ReactNode;
}) {
  return (
    <details
      className="sketch max-w-full rotate-(--tilt) bg-paper transition-[rotate] duration-150 open:basis-full open:rotate-0"
      style={{ "--tilt": `${tilt}deg` } as React.CSSProperties}
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 py-1.5 pr-3 pl-7 text-sm [&::-webkit-details-marker]:hidden">
        <PaperclipIcon size={20} className="pointer-events-none absolute -top-2.5 left-2 rotate-[-18deg] text-ink" />
        <span className="grid size-[1.375rem] shrink-0 place-items-center rounded-full border-[1.5px] border-ink bg-flower text-[0.8125rem] leading-none font-semibold">
          {index}
        </span>
        <span className="min-w-0 truncate" title={full}>{title}</span>
        {score && <span className="shrink-0 text-[0.8125rem] text-muted tabular-nums">{score}</span>}
      </summary>
      <div className="border-t border-dashed border-rule px-3 pt-2 pb-3 text-sm leading-relaxed text-muted">
        {children}
      </div>
    </details>
  );
}

function SourceSlips({ sources }: { sources: ChatSource[] }) {
  return (
    <div className="mt-4">
      <p className="mb-2 text-sm text-muted">出处</p>
      <div className="flex flex-wrap gap-2.5">
        {sources.map((source, i) => (
          <Slip
            key={`${source.index}-${source.source}`}
            tilt={i % 2 ? 0.8 : -0.6}
            index={source.index}
            title={source.url ? source.source : sourceTitle(source.source)}
            full={source.source}
            score={scoreLabel(source)}
          >
            {source.snippet ? (
              <p className="whitespace-pre-wrap">{source.snippet}{source.url ? "" : "…"}</p>
            ) : (
              !source.url && <p>整篇精读，未截取片段。</p>
            )}
            {source.url && (
              <a href={source.url} target="_blank" rel="noreferrer" className="mt-1 inline-block break-all text-ink underline decoration-dashed underline-offset-2">
                打开链接
              </a>
            )}
          </Slip>
        ))}
      </div>
    </div>
  );
}

export function MessageBubble({ message, streaming = false, signed = false, showDebug = false }: Props) {
  const sources = message.sources ?? [];

  if (message.role === "user") {
    return (
      <div className="flex justify-end" data-role="user">
        <p className="max-w-[85%] text-right leading-[1.9] whitespace-pre-wrap break-words">
          <span className="marker marker-swipe">{message.content}</span>
        </p>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <p className="leading-[1.75] whitespace-pre-wrap break-words">
        {message.content}
        {streaming && <PencilIcon size={18} className="pencil-bob ml-1 inline-block align-[-3px] text-pink" />}
        {signed && <Flower size={20} className="flower-draw ml-1.5 inline-block align-[-4px]" />}
      </p>

      {message.safety && <SafetyCard card={message.safety} />}

      {sources.length > 0 && <SourceSlips sources={sources} />}

      {sources.length === 0 && message.context && (
        <div className="mt-4 flex">
          <Slip index="※" title="资料原文" full="知识库里检索到的原文">
            <pre className="max-h-64 overflow-auto font-body whitespace-pre-wrap">{message.context}</pre>
          </Slip>
        </div>
      )}

      {showDebug && message.emotion && (
        <details className="mt-3 text-sm text-muted">
          <summary className="cursor-pointer">情感分析（仅管理员可见）</summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap">
            {JSON.stringify(message.emotion, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
