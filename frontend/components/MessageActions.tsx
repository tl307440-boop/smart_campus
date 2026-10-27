"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { CheckIcon, CopyIcon, PencilIcon, RedoIcon, SpeakerIcon, ThumbIcon } from "@/components/SketchIcons";
import { useToast } from "@/components/Toast";
import { useCopy } from "@/hooks/useCopy";
import type { FeedbackReason, FeedbackRating } from "@/lib/api";
import { speakableText, speakText, useSpeechSettings } from "@/lib/speech-settings";

/** 这条回答能不能点赞点踩，以及现在是什么状态 */
export interface RatingProps {
  value: FeedbackRating;
  reason: FeedbackReason;
  onRate: (rating: FeedbackRating, reason?: FeedbackReason) => Promise<void>;
}

interface Props {
  text: string;
  align: "start" | "end";
  /** 最新一条：控件常驻；更早的只在悬停 / 聚焦时出现 */
  pinned?: boolean;
  onRegenerate?: () => void;
  onEdit?: () => void;
  speakable?: boolean;
  /** 只有入库的助手回答才有 */
  rating?: RatingProps;
}

const DOWN_REASONS: { value: Exclude<FeedbackReason, "">; label: string }[] = [
  { value: "wrong", label: "答非所问" },
  { value: "outdated", label: "信息过期" },
  { value: "verbose", label: "太啰嗦" },
  { value: "other", label: "其他" },
];

function ActionButton({
  label,
  onClick,
  pressed,
  children,
}: {
  label: string;
  onClick: () => void;
  /** 开关类按钮的当前状态；普通按钮不传 */
  pressed?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={pressed}
      title={label}
      className={`grid size-8 place-items-center rounded-full transition-colors hover:bg-skin hover:text-ink ${pressed ? "text-pink" : "text-muted"}`}
    >
      {children}
    </button>
  );
}

/** 左右方向键在控件之间移动，Tab 只停一次 */
function moveFocus(event: KeyboardEvent<HTMLDivElement>) {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  const buttons = [...event.currentTarget.querySelectorAll("button")];
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
  const next = buttons[(index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length];
  next?.focus();
  event.preventDefault();
}

/** 消息下面的一排小控件：复制、重新生成、朗读、改了再问、点赞点踩。 */
export function MessageActions({ text, align, pinned = false, onRegenerate, onEdit, speakable = false, rating }: Props) {
  const { copied, copy } = useCopy();
  const toast = useToast();
  const [speech] = useSpeechSettings();
  const [speaking, setSpeaking] = useState(false);
  const [announce, setAnnounce] = useState("");
  // 刚点了踩、还没选原因：多问一句。从历史里打开的旧评价不再追问
  const [asking, setAsking] = useState(false);
  const speakingRef = useRef(false);

  useEffect(() => {
    speakingRef.current = speaking;
  }, [speaking]);

  // 只在这条消息离开页面（换会话、被重新生成替换）时停掉它自己的朗读
  useEffect(() => () => {
    if (speakingRef.current) window.speechSynthesis?.cancel();
  }, []);

  async function handleCopy() {
    if (await copy(text)) setAnnounce("已复制");
  }

  function toggleSpeak() {
    if (speaking) {
      window.speechSynthesis?.cancel();
      setSpeaking(false);
      return;
    }
    const utterance = speakText(speakableText(text), speech);
    if (!utterance) return;
    setSpeaking(true);
    const done = () => setSpeaking(false);
    utterance.addEventListener("end", done);
    utterance.addEventListener("error", done);
  }

  /** 点已选中的那个就是取消 */
  async function handleRate(next: 1 | -1, reason: FeedbackReason = "") {
    if (!rating) return;
    const cancel = rating.value === next && !reason;
    try {
      await rating.onRate(cancel ? 0 : next, reason);
      setAsking(!cancel && next === -1);
      setAnnounce(cancel ? "已取消评价" : next === 1 ? "已点赞" : "已记下，谢谢反馈");
    } catch {
      setAsking(false);
      toast.error("评价没记上，稍后再试");
    }
  }

  async function handleReason(reason: Exclude<FeedbackReason, "">) {
    await handleRate(-1, reason);
    setAsking(false);
  }

  const reveal = pinned
    ? ""
    : "[@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 [@media(hover:hover)]:focus-within:opacity-100";

  return (
    <>
      <div
        role="toolbar"
        aria-label="这条消息的操作"
        onKeyDown={moveFocus}
        className={`mt-1 flex gap-0.5 transition-opacity duration-150 ${align === "end" ? "justify-end" : ""} ${reveal}`}
      >
        <ActionButton label={copied ? "已复制" : "复制"} onClick={handleCopy}>
          {copied ? <CheckIcon size={17} className="text-ink" /> : <CopyIcon size={17} />}
        </ActionButton>
        {onEdit && (
          <ActionButton label="改一改再问" onClick={onEdit}>
            <PencilIcon size={17} />
          </ActionButton>
        )}
        {onRegenerate && (
          <ActionButton label="重新回答" onClick={onRegenerate}>
            <RedoIcon size={17} />
          </ActionButton>
        )}
        {speakable && "speechSynthesis" in globalThis && (
          <ActionButton label={speaking ? "停止朗读" : "念给我听"} onClick={toggleSpeak}>
            <SpeakerIcon on={!speaking} size={17} className={speaking ? "text-pink" : undefined} />
          </ActionButton>
        )}
        {rating && (
          <>
            <ActionButton label="回答得好" pressed={rating.value === 1} onClick={() => void handleRate(1)}>
              <ThumbIcon size={17} filled={rating.value === 1} />
            </ActionButton>
            <ActionButton label="回答得不好" pressed={rating.value === -1} onClick={() => void handleRate(-1)}>
              <ThumbIcon size={17} down filled={rating.value === -1} />
            </ActionButton>
          </>
        )}
        <span className="sr-only" aria-live="polite">
          {announce}
        </span>
      </div>

      {rating && asking && rating.value === -1 && (
        <div role="group" aria-label="哪里不满意" className="rise-in mt-1 flex flex-wrap items-center gap-1.5 text-sm text-muted">
          <span>哪里不好？</span>
          {DOWN_REASONS.map((item) => (
            <button
              key={item.value}
              type="button"
              aria-pressed={rating.reason === item.value}
              onClick={() => void handleReason(item.value)}
              className={`rounded-full border px-2.5 py-0.5 transition-colors hover:bg-skin ${
                rating.reason === item.value ? "border-pink text-ink" : "border-rule"
              }`}
            >
              {item.label}
            </button>
          ))}
          <button type="button" onClick={() => setAsking(false)} className="px-1.5 underline decoration-dashed underline-offset-2 hover:text-ink">
            不说了
          </button>
        </div>
      )}
    </>
  );
}
