"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** 「已复制」停留多久再变回原样 */
const COPIED_MS = 1500;

/** http 页面或老浏览器没有 navigator.clipboard，退回选中隐藏文本框再复制 */
function legacyCopy(text: string): boolean {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand("copy");
  } finally {
    area.remove();
  }
}

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 权限被拒时也走老办法
  }
  return legacyCopy(text);
}

export function useCopy() {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = useCallback(async (text: string) => {
    const ok = await copyText(text);
    if (!ok) return false;
    setCopied(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), COPIED_MS);
    return true;
  }, []);

  return { copied, copy };
}
