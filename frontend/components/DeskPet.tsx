"use client";

import { type CSSProperties, type ReactNode, useId, useSyncExternalStore } from "react";
import {
  type Particle,
  PET_EYES,
  PET_HEIGHT,
  PET_VIEWBOX,
  PET_WIDTH,
  type PetMood,
  useDeskPet,
} from "@/hooks/useDeskPet";
import styles from "./DeskPet.module.css";

interface Props {
  thinking: boolean;
  /** 本轮回答是否出错：结束时不欢呼，改为吃惊 */
  failed?: boolean;
  onOpenSettings: () => void;
}

const INK = "#1b1214";
const SKIN = "#fde3e9";
const LID = "#f9d6de";
const MOUTH = "#e8487e";
const MOUTH_CENTER = { x: 336, y: 339 };

type ClosedEyes = "sleep" | "smile" | "squeeze" | "spiral";
const CLOSED_EYES: Partial<Record<PetMood, ClosedEyes>> = {
  sleepy: "sleep",
  happy: "smile",
  petted: "smile",
  dragging: "squeeze",
  dizzy: "spiral",
};

type Eye = (typeof PET_EYES)[number];

function spiral(cx: number, cy: number, r: number): string {
  const points = Array.from({ length: 41 }, (_, i) => {
    const t = i / 40;
    const a = t * Math.PI * 4.5;
    return `${(cx + Math.cos(a) * r * t).toFixed(1)} ${(cy + Math.sin(a) * r * t).toFixed(1)}`;
  });
  return `M${points.join(" L")}`;
}

function closedEyePath(kind: ClosedEyes, e: Eye, side: number): string {
  const { cx, cy, rx, ry } = e;
  switch (kind) {
    case "sleep":
      return `M${cx - rx * 0.62} ${cy - 2} Q${cx} ${cy + ry * 0.45} ${cx + rx * 0.62} ${cy - 2}`;
    case "smile":
      return `M${cx - rx * 0.6} ${cy + ry * 0.22} Q${cx} ${cy - ry * 0.5} ${cx + rx * 0.6} ${cy + ry * 0.22}`;
    case "squeeze": {
      const tip = cx + side * rx * 0.32;
      const back = cx - side * rx * 0.45;
      return `M${back} ${cy - ry * 0.4} L${tip} ${cy} L${back} ${cy + ry * 0.4}`;
    }
    case "spiral":
      return spiral(cx, cy, ry * 0.62);
  }
}

function PetEye({ eye, index, clipId, mood, closed }: {
  eye: Eye; index: number; clipId: string; mood: PetMood; closed?: ClosedEyes;
}) {
  const look = mood === "thinking"
    ? `translate(${eye.thinkX}px, ${eye.thinkY}px)`
    : `translate(var(--p${index}x, 0px), var(--p${index}y, 0px))`;
  return (
    <>
      <clipPath id={clipId}>
        <ellipse cx={eye.cx} cy={eye.cy} rx={eye.rx} ry={eye.ry} />
      </clipPath>
      <g clipPath={`url(#${clipId})`}>
        <g style={{ transform: look }}>
          <g className={styles.pupil}>
            <ellipse cx={eye.cx} cy={eye.cy} rx={11} ry={14} fill={INK} />
            <circle cx={eye.cx + 4} cy={eye.cy - 5} r={3.6} fill="#fff" />
          </g>
        </g>
        <rect
          className={`${styles.lid} ${closed ? styles.lidClosed : ""}`}
          x={eye.cx - eye.rx - 4}
          y={eye.cy - eye.ry - 4}
          width={eye.rx * 2 + 8}
          height={eye.ry * 2 + 8}
          fill={LID}
        />
      </g>
      {closed && (
        <path
          className={closed === "spiral" ? styles.spiral : undefined}
          d={closedEyePath(closed, eye, index === 0 ? 1 : -1)}
          fill="none"
          stroke={INK}
          strokeWidth={closed === "spiral" ? 6 : 8}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </>
  );
}

function PetMouth({ mood }: { mood: PetMood }) {
  const { x, y } = MOUTH_CENTER;
  let shape: ReactNode = null;
  if (mood === "sleepy") {
    shape = <ellipse cx={x} cy={y - 2} rx={9} ry={7} fill={MOUTH} stroke={INK} strokeWidth={5} />;
  } else if (mood === "surprised") {
    shape = <ellipse cx={x} cy={y} rx={17} ry={21} fill={MOUTH} stroke={INK} strokeWidth={6} />;
  } else if (mood === "dizzy") {
    shape = (
      <path d={`M${x - 30} ${y} q7.5 -10 15 0 t15 0 t15 0 t15 0`} fill="none" stroke={INK} strokeWidth={6} strokeLinecap="round" />
    );
  } else if (mood === "thinking") {
    shape = (
      <path d={`M${x - 18} ${y + 3} Q${x} ${y - 6} ${x + 16} ${y + 5}`} fill="none" stroke={INK} strokeWidth={6} strokeLinecap="round" />
    );
  }
  if (!shape) return null;
  return (
    <>
      <ellipse cx={x} cy={y} rx={45} ry={42} fill={SKIN} />
      {shape}
    </>
  );
}

function Flower() {
  return (
    <svg viewBox="-10 -10 20 20" width="18" height="18" aria-hidden="true">
      {[0, 72, 144, 216, 288].map((deg) => (
        <ellipse key={deg} cx="0" cy="-5" rx="3.6" ry="5" fill="#7cc8f2" stroke={INK} strokeWidth="0.8" transform={`rotate(${deg})`} />
      ))}
      <circle r="3" fill="#ffd34d" stroke={INK} strokeWidth="0.8" />
    </svg>
  );
}

function Particles({ items }: { items: Particle[] }) {
  return items.map((p) => (
    <span
      key={p.id}
      className={styles.particle}
      style={{
        "--dx": `${p.dx}px`, "--dy": `${p.dy}px`, "--rot": `${p.rot}deg`,
        animationDelay: `${p.delay}ms`,
      } as CSSProperties}
    >
      {p.kind === "flower" ? <Flower /> : <span className={styles.heart}>♥</span>}
    </span>
  ));
}

function DeskPetInner({ thinking, failed = false, onOpenSettings }: Props) {
  const id = useId().replace(/:/g, "");
  const { rootRef, pos, mood, blink, pressed, bubble, particles, bounce, handlers } = useDeskPet({
    thinking,
    failed,
    onOpenSettings,
  });
  const closed = blink && mood === "normal" ? "sleep" : CLOSED_EYES[mood];
  const bounceClass = bounce.kind ? styles[`${bounce.kind}${bounce.n % 2 ? "A" : "B"}`] : "";

  return (
    <div
      ref={rootRef}
      role="button"
      tabIndex={0}
      aria-label="校园桌宠：单击戳一戳，拖动换位置，双击配置模型"
      title="双击配置模型"
      className={styles.pet}
      data-mood={mood}
      data-pressed={pressed || undefined}
      data-below={pos.y < 96 || undefined}
      data-flip={pos.x < 180 || undefined}
      style={{ left: pos.x, top: pos.y, width: PET_WIDTH, height: PET_HEIGHT }}
      {...handlers}
    >
      {bubble && <div className={styles.bubble}>{bubble}</div>}
      {mood === "thinking" && (
        <div className={styles.think} aria-hidden="true"><i /><i /><i /></div>
      )}
      {mood === "surprised" && <span className={styles.exclaim} aria-hidden="true">!</span>}
      <div className={styles.body}>
        <div className={`${styles.motion} ${bounceClass}`}>
          <svg viewBox={`0 0 ${PET_VIEWBOX.w} ${PET_VIEWBOX.h}`} className={styles.svg} aria-hidden="true">
            <image href="/pet/girl.webp" width={PET_VIEWBOX.w} height={PET_VIEWBOX.h} />
            {(mood === "petted" || mood === "happy") && (
              <g className={styles.blush}>
                <ellipse cx={230} cy={324} rx={40} ry={17} fill="#ff6f91" />
                <ellipse cx={425} cy={324} rx={40} ry={17} fill="#ff6f91" />
              </g>
            )}
            {PET_EYES.map((eye, i) => (
              <PetEye key={i} eye={eye} index={i} clipId={`${id}-eye${i}`} mood={mood} closed={closed} />
            ))}
            <PetMouth mood={mood} />
            {mood === "sleepy" && (
              <g className={styles.noseBubble}>
                <circle cx={326} cy={268} r={20} fill="rgba(170, 215, 255, 0.45)" stroke="#6fb0e6" strokeWidth={3} />
                <circle cx={319} cy={261} r={5} fill="rgba(255, 255, 255, 0.85)" />
              </g>
            )}
          </svg>
        </div>
      </div>
      {mood === "sleepy" && (
        <div className={styles.zzz} aria-hidden="true"><span>z</span><span>Z</span><span>z</span></div>
      )}
      {mood === "dizzy" && (
        <div className={styles.orbit} aria-hidden="true">
          <div className={styles.ring}><span>★</span><span>★</span><span>★</span></div>
        </div>
      )}
      <Particles items={particles} />
    </div>
  );
}

const subscribe = () => () => {};

/** 桌宠：眼睛跟着鼠标转，闲置会睡着；单击戳一戳、摸头、拖动，双击打开 AI 服务设置。 */
export function DeskPet(props: Props) {
  const mounted = useSyncExternalStore(subscribe, () => true, () => false);
  return mounted ? <DeskPetInner {...props} /> : null;
}
