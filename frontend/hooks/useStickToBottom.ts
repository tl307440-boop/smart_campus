"use client";

import { type RefObject, useCallback, useEffect, useRef, useState } from "react";

/** 离底部这么近就算「在看最新」，新内容来了跟着滚 */
const NEAR_BOTTOM_PX = 120;

/**
 * 只有用户本来就在底部时才自动滚到最新；他往上翻着看旧回答时不打扰，
 * 改为亮出「回到最新」按钮，有新内容时带个小圆点。
 */
export function useStickToBottom(scrollRef: RefObject<HTMLElement | null>, contentKey: unknown) {
  const [atBottom, setAtBottom] = useState(true);
  const [unseen, setUnseen] = useState(false);
  const atBottomRef = useRef(true);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const onScroll = () => {
      const near = element.scrollHeight - element.scrollTop - element.clientHeight < NEAR_BOTTOM_PX;
      atBottomRef.current = near;
      setAtBottom(near);
      if (near) setUnseen(false);
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    return () => element.removeEventListener("scroll", onScroll);
  }, [scrollRef]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    if (atBottomRef.current) {
      element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
      return;
    }
    setUnseen(true);
  }, [scrollRef, contentKey]);

  const scrollToBottom = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    atBottomRef.current = true;
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
    setUnseen(false);
  }, [scrollRef]);

  return { atBottom, unseen, scrollToBottom };
}
