"use client";

import { type KeyboardEvent, useState } from "react";
import { DataTable } from "@/components/DataTable";
import { Flower } from "@/components/DeskPet";
import { Markdown } from "@/components/Markdown";
import { MessageActions } from "@/components/MessageActions";
import { SafetyCard } from "@/components/SafetyCard";
import { PaperclipIcon, PencilIcon } from "@/components/SketchIcons";
import type { ChatMessage, ChatSource, ChatToolEvent } from "@/lib/api";

interface Props {
  message: ChatMessage;
  streaming?: boolean;
  /** 最新一条答完的回答：末尾画一朵蓝花 */
  signed?: boolean;
  /** 管理员排查用：情感分析等调试信息 */
  showDebug?: boolean;
  /** 不显示复制、重答这些控件（流式中的那条） */
  bare?: boolean;
  /** 属于最后一轮：控件常驻，并且可以重新回答 / 改了再问 */
  latest?: boolean;
  onRegenerate?: () => void;
  onEdit?: (text: string) => void;
}

function ToolTrace({ tools }: { tools: ChatToolEvent[] }) {
  const finished = tools.filter((tool) => tool.state === "done");
  if (!finished.length) return null;
  return (
    <details className="mt-3 text-sm text-muted">
      <summary className="cursor-pointer select-none">
        查了 {finished.length} 处：{finished.map((tool) => tool.label).join("、")}
      </summary>
      <ul className="mt-1.5 space-y-0.5 pl-4">
        {finished.map((tool) => (
          <li key={tool.name} className="list-disc">
            {tool.label}
            {tool.ok === false && <span className="ml-1.5 text-danger">没查成</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

function EditBox({ initial, onSubmit, onCancel }: { initial: string; onSubmit: (text: string) => void; onCancel: () => void }) {
  const [value, setValue] = useState(initial);
  const text = value.trim();

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape") onCancel();
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (text) onSubmit(text);
    }
  }

  return (
    <div className="flex w-full max-w-[85%] flex-col items-end gap-2">
      <textarea
        autoFocus
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        aria-label="改一改你的问题"
        className="field field-sizing-content max-h-48 min-h-11 w-full resize-none text-right"
      />
      <div className="flex gap-2">
        <button type="button" onClick={onCancel} className="btn btn-sm">
          算了
        </button>
        <button type="button" disabled={!text} onClick={() => onSubmit(text)} className="btn btn-sm btn-primary">
          改好了，重新问
        </button>
      </div>
    </div>
  );
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

export function MessageBubble({
  message,
  streaming = false,
  signed = false,
  showDebug = false,
  bare = false,
  latest = false,
  onRegenerate,
  onEdit,
}: Props) {
  const [editing, setEditing] = useState(false);
  const sources = message.sources ?? [];
  const tables = message.tables ?? [];
  const showActions = !bare && !streaming;

  if (message.role === "user") {
    return (
      <div className="group flex flex-col items-end" data-role="user">
        {editing && onEdit ? (
          <EditBox
            initial={message.content}
            onCancel={() => setEditing(false)}
            onSubmit={(text) => {
              setEditing(false);
              onEdit(text);
            }}
          />
        ) : (
          <p className="max-w-[85%] text-right leading-[1.9] whitespace-pre-wrap break-words">
            <span className="marker marker-swipe">{message.content}</span>
          </p>
        )}
        {showActions && !editing && (
          <MessageActions
            text={message.content}
            align="end"
            pinned={latest}
            onEdit={latest && onEdit ? () => setEditing(true) : undefined}
          />
        )}
      </div>
    );
  }

  return (
    <div className="group animate-fade-in" data-role="assistant">
      <Markdown
        text={message.content}
        tail={
          streaming || signed ? (
            <>
              {streaming && <PencilIcon size={18} className="pencil-bob ml-1 inline-block align-[-3px] text-pink" />}
              {signed && <Flower size={20} className="flower-draw ml-1.5 inline-block align-[-4px]" />}
            </>
          ) : null
        }
      />

      {tables.map((table, i) => (
        <DataTable key={`${table.title}-${i}`} table={table} />
      ))}

      {message.safety && <SafetyCard card={message.safety} />}

      {message.tools && <ToolTrace tools={message.tools} />}

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

      {showActions && (
        <MessageActions
          text={message.content}
          align="start"
          pinned={latest}
          onRegenerate={latest ? onRegenerate : undefined}
          speakable
        />
      )}
    </div>
  );
}
