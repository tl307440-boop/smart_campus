"use client";

import { type RefObject, useEffect, useState } from "react";
import { copyText } from "@/hooks/useCopy";

/** 浮层离选区上沿多远 */
const GAP_PX = 8;
/** 太短的选区（双击选中一个字）不弹 */
const MIN_CHARS = 2;

interface Anchor {
  text: string;
  x: number;
  y: number;
}

interface Props {
  /** 只认这个容器里助手回答（data-role=assistant）中的选区 */
  containerRef: RefObject<HTMLElement | null>;
  onAsk: (text: string) => void;
}

function selectionInAnswer(container: HTMLElement | null): Anchor | null {
  const selection = window.getSelection();
  if (!container || !selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const text = selection.toString().trim();
  if (text.length < MIN_CHARS) return null;
  const range = selection.getRangeAt(0);
  const node = range.commonAncestorContainer;
  const element = node instanceof Element ? node : node.parentElement;
  if (!element || !container.contains(element) || !element.closest("[data-role=assistant]")) return null;
  const rect = range.getBoundingClientRect();
  return { text, x: rect.left + rect.width / 2, y: rect.top };
}

/** 在回答里划一段字，上方浮出「复制」和「就这段再问」。 */
export function SelectionAsk({ containerRef, onAsk }: Props) {
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  useEffect(() => {
    const update = () => setAnchor(selectionInAnswer(containerRef.current));
    const hide = () => setAnchor(null);
    const onSelectionChange = () => {
      if (window.getSelection()?.isCollapsed) hide();
    };
    document.addEventListener("pointerup", update);
    document.addEventListener("keyup", update);
    document.addEventListener("selectionchange", onSelectionChange);
    const scroller = containerRef.current;
    scroller?.addEventListener("scroll", hide, { passive: true });
    return () => {
      document.removeEventListener("pointerup", update);
      document.removeEventListener("keyup", update);
      document.removeEventListener("selectionchange", onSelectionChange);
      scroller?.removeEventListener("scroll", hide);
    };
  }, [containerRef]);

  if (!anchor) return null;

  function finish() {
    window.getSelection()?.removeAllRanges();
    setAnchor(null);
  }

  return (
    <div
      className="sketch pop-in fixed z-30 flex -translate-x-1/2 -translate-y-full gap-1 bg-paper p-1 [animation-duration:160ms]"
      style={{ left: anchor.x, top: anchor.y - GAP_PX }}
      // 按下时别让选区先没了
      onPointerDown={(event) => event.preventDefault()}
    >
      <button
        type="button"
        className="rounded-lg px-2.5 py-1 text-sm hover:bg-skin"
        onClick={() => {
          void copyText(anchor.text);
          finish();
        }}
      >
        复制
      </button>
      <button
        type="button"
        className="rounded-lg px-2.5 py-1 text-sm hover:bg-skin"
        onClick={() => {
          onAsk(anchor.text);
          finish();
        }}
      >
        就这段再问
      </button>
    </div>
  );
}
