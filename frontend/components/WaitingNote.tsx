"use client";

import { useEffect, useState } from "react";
import { PetPortrait } from "@/components/PetPortrait";
import { type ChatProgress, doneText, progressCeiling, progressText, stepKey, whispers } from "@/lib/chat-progress";

const WHISPER_MS = 2600;
const CREEP_MS = 120;
const MAX_DONE_SHOWN = 3;

function useTicker(ms: number) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((n) => n + 1), ms);
    return () => window.clearInterval(timer);
  }, [ms]);
  return tick;
}

/** 每一拍往上限挪一小截，越靠近越慢；上限只升不降，所以条子不会倒退 */
function useCreep(ceiling: number) {
  const [value, setValue] = useState(5);
  useEffect(() => {
    const timer = window.setInterval(() => setValue((v) => v + Math.max(0, ceiling - v) * 0.05), CREEP_MS);
    return () => window.clearInterval(timer);
  }, [ceiling]);
  return value;
}

function Check() {
  return (
    <svg viewBox="0 0 14 14" className="size-3.5 shrink-0 overflow-visible" aria-hidden="true">
      <path
        d="M2 7.5 5.5 11 12.5 3"
        className="waiting-check"
        fill="none"
        stroke="var(--color-pink)"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** 笔尖在 (1, 23)：外层定位到进度条末端，笔尖正好落在线上 */
function Pencil() {
  return (
    <svg viewBox="0 0 24 24" className="waiting-pencil absolute -bottom-px -left-px size-5 overflow-visible" aria-hidden="true">
      <g transform="rotate(-32 1 23)" stroke="var(--color-ink)" strokeWidth="1.2" strokeLinejoin="round">
        <path d="M1 23 7 19.8V26.2Z" fill="var(--color-skin)" />
        <path d="M1 23 3.2 21.8V24.2Z" fill="var(--color-ink)" />
        <rect x="7" y="19.8" width="15" height="6.4" fill="var(--color-pollen)" />
        <rect x="22" y="19.8" width="5" height="6.4" rx="1.6" fill="var(--color-pink)" />
      </g>
    </svg>
  );
}

/** 第一个字出来之前的等待：桌宠在想、马克笔进度条往前涂、走过的步骤打勾、底下轮换几句小字 */
export function WaitingNote({ progress, name }: { progress: ChatProgress; name: string }) {
  const key = stepKey(progress);
  const tick = useTicker(WHISPER_MS);
  const [steps, setSteps] = useState<ChatProgress[]>([progress]);
  const [stepTick, setStepTick] = useState(0);
  const [wasSlow, setWasSlow] = useState(progress.slow);
  if (stepKey(steps[steps.length - 1]) !== key) {
    setSteps([...steps.filter((step) => stepKey(step) !== key), progress]);
    setStepTick(tick);
  }
  if (wasSlow !== progress.slow) {
    setWasSlow(progress.slow);
    setStepTick(tick);
  }

  const done = steps.slice(0, -1).slice(-MAX_DONE_SHOWN);
  const value = useCreep(progressCeiling(progress.phase, steps.length));
  const lines = whispers(progress);
  const whisper = lines[(tick - stepTick) % lines.length];
  const text = progressText(progress, name);

  return (
    <div className="animate-fade-in flex items-center gap-3" role="status">
      <span className="relative shrink-0">
        <PetPortrait eyesClosed={false} className="pencil-bob size-10 [animation-duration:1.4s]" />
        <span className="absolute -top-3.5 right-0 h-4 w-4" aria-hidden="true">
          {[4, 6, 8].map((size, i) => (
            <i
              key={size}
              className="waiting-puff absolute rounded-full border border-ink bg-paper"
              style={{ width: size, height: size, left: i * 4, bottom: i * 4, animationDelay: `${i * 0.25}s` }}
            />
          ))}
        </span>
      </span>

      <div className="min-w-0 flex-1">
        <p key={text} className="rise-in text-[0.9375rem] leading-snug text-muted">
          {text}
        </p>

        <div className="relative mt-2.5 h-3 w-full max-w-64" aria-hidden="true">
          <span className="absolute inset-x-0 top-1/2 border-t-2 border-dashed border-rule" />
          <span className="waiting-fill absolute top-1/2 left-0 h-2.5 -translate-y-1/2" style={{ width: `${value}%` }} />
          <span className="absolute top-1/2 size-0" style={{ left: `${value}%` }}>
            <Pencil />
          </span>
        </div>

        <div className="mt-1 flex min-h-5 flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs leading-5 text-muted">
          {done.map((step) => (
            <span key={stepKey(step)} className="pop-in inline-flex items-center gap-0.5 text-ink/70">
              <Check />
              {doneText(step)}
            </span>
          ))}
          <span key={`${key}-${whisper}`} className="rise-in" aria-hidden="true">
            {whisper}…
          </span>
        </div>
      </div>
    </div>
  );
}
