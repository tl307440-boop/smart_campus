"use client";

import { type RefObject, useEffect, useId, useRef, useState } from "react";
import { PetEye, PetMouth } from "@/components/DeskPet";
import { PET_EYES, PET_VIEWBOX, type PetMood } from "@/hooks/useDeskPet";

interface Props {
  /** 输密码时闭眼 */
  eyesClosed: boolean;
  mood?: Extract<PetMood, "normal" | "surprised">;
  className?: string;
}

/**
 * 瞳孔看向光标；在输入框里打字时看向光标所在的那一格。
 * 和 useDeskPet 一样直接写 CSS 变量，不触发 React 渲染。
 */
function usePortraitGaze(ref: RefObject<SVGSVGElement | null>) {
  useEffect(() => {
    const svg = ref.current;
    if (!svg) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const cur = PET_EYES.map(() => ({ x: 0, y: 0 }));
    const goal = PET_EYES.map(() => ({ x: 0, y: 0 }));
    let raf = 0;

    const aim = (px: number, py: number) => {
      const rect = svg.getBoundingClientRect();
      const scale = rect.width / PET_VIEWBOX.w;
      PET_EYES.forEach((e, i) => {
        const ex = px - (rect.left + e.cx * scale);
        const ey = py - (rect.top + e.cy * scale);
        const d = Math.hypot(ex, ey) || 1;
        const reach = Math.min(d / 360, 1);
        goal[i] = { x: (ex / d) * reach * (e.rx - 22), y: (ey / d) * reach * (e.ry - 24) };
      });
      if (!raf) raf = window.requestAnimationFrame(step);
    };

    const step = () => {
      raf = 0;
      let moving = false;
      cur.forEach((p, i) => {
        p.x += (goal[i].x - p.x) * (reduce ? 1 : 0.2);
        p.y += (goal[i].y - p.y) * (reduce ? 1 : 0.2);
        if (Math.abs(goal[i].x - p.x) > 0.05 || Math.abs(goal[i].y - p.y) > 0.05) moving = true;
        svg.style.setProperty(`--p${i}x`, `${p.x.toFixed(1)}px`);
        svg.style.setProperty(`--p${i}y`, `${p.y.toFixed(1)}px`);
      });
      if (moving) raf = window.requestAnimationFrame(step);
    };

    const onMove = (ev: PointerEvent) => aim(ev.clientX, ev.clientY);
    const onType = (ev: Event) => {
      const el = ev.target;
      if (!(el instanceof HTMLInputElement)) return;
      const r = el.getBoundingClientRect();
      const caret = Math.min(r.width - 8, (el.selectionStart ?? el.value.length) * 9);
      aim(r.left + caret, r.top + r.height / 2);
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("input", onType);
    document.addEventListener("focusin", onType);
    return () => {
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("input", onType);
      document.removeEventListener("focusin", onType);
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, [ref]);
}

/** 登录页的桌宠大头：复用桌宠的画稿、眼睛和嘴，只是不能拖。 */
export function PetPortrait({ eyesClosed, mood = "normal", className }: Props) {
  const id = useId().replace(/:/g, "");
  const svgRef = useRef<SVGSVGElement>(null);
  const [blink, setBlink] = useState(false);
  usePortraitGaze(svgRef);

  useEffect(() => {
    let next = 0;
    let reopen = 0;
    const loop = () => {
      next = window.setTimeout(() => {
        setBlink(true);
        reopen = window.setTimeout(() => setBlink(false), 130);
        loop();
      }, 2200 + Math.random() * 2800);
    };
    loop();
    return () => {
      window.clearTimeout(next);
      window.clearTimeout(reopen);
    };
  }, []);

  const closed = eyesClosed || blink ? "sleep" : undefined;
  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${PET_VIEWBOX.w} ${PET_VIEWBOX.h}`}
      className={className}
      role="img"
      aria-label={eyesClosed ? "校园助手闭上眼睛，不看你的密码" : "校园助手在看着你"}
    >
      <image href="/pet/girl.webp" width={PET_VIEWBOX.w} height={PET_VIEWBOX.h} />
      {PET_EYES.map((eye, i) => (
        <PetEye key={i} eye={eye} index={i} clipId={`${id}-eye${i}`} mood="normal" closed={closed} />
      ))}
      <PetMouth mood={mood} />
    </svg>
  );
}
