"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { CheckIcon, CopyIcon, PencilIcon, RedoIcon, SpeakerIcon } from "@/components/SketchIcons";
import { useCopy } from "@/hooks/useCopy";
import { speakableText, speakText, useSpeechSettings } from "@/lib/speech-settings";

interface Props {
  text: string;
  align: "start" | "end";
  /** 最新一条：控件常驻；更早的只在悬停 / 聚焦时出现 */
  pinned?: boolean;
  onRegenerate?: () => void;
  onEdit?: () => void;
  speakable?: boolean;
}

function ActionButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="grid size-8 place-items-center rounded-full text-muted transition-colors hover:bg-skin hover:text-ink"
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

/** 消息下面的一排小控件：复制、重新生成、朗读、改了再问。 */
export function MessageActions({ text, align, pinned = false, onRegenerate, onEdit, speakable = false }: Props) {
  const { copied, copy } = useCopy();
  const [speech] = useSpeechSettings();
  const [speaking, setSpeaking] = useState(false);
  const [announce, setAnnounce] = useState("");
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

  const reveal = pinned
    ? ""
    : "[@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 [@media(hover:hover)]:focus-within:opacity-100";

  return (
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
      <span className="sr-only" aria-live="polite">
        {announce}
      </span>
    </div>
  );
}
