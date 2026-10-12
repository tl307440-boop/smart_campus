"use client";

import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

export type PetMood =
  | "normal"
  | "sleepy"
  | "happy"
  | "surprised"
  | "dizzy"
  | "thinking"
  | "dragging"
  | "petted";

export interface Particle {
  id: number;
  kind: "flower" | "heart";
  dx: number;
  dy: number;
  rot: number;
  delay: number;
}

interface Point {
  x: number;
  y: number;
}

/** 素材 public/pet/girl.webp 的坐标系；眼睛参数在这个坐标里量得 */
export const PET_VIEWBOX = { w: 646, h: 634 } as const;
export const PET_EYES = [
  { cx: 247, cy: 257, rx: 60, ry: 54, restX: 280, restY: 241, thinkX: 24, thinkY: -20 },
  { cx: 398, cy: 248, rx: 65, ry: 55, restX: 367, restY: 237, thinkX: 26, thinkY: -20 },
] as const;
export const PET_WIDTH = 128;
export const PET_HEIGHT = Math.round((PET_WIDTH * PET_VIEWBOX.h) / PET_VIEWBOX.w);

const POSITION_KEY = "zhiyue.deskpet.position";
const HINT_KEY = "zhiyue.deskpet.hinted";
const EDGE = 8;
const DRAG_THRESHOLD = 5;
const SLEEP_AFTER_MS = 6000;
const BLINK_MS = 130;
const POKE_LINES = ["干嘛戳我～", "嘿嘿", "今天也要加油鸭！", "有问题尽管问我", "双击我可以配置模型哦"];
const PET_LINES = ["好舒服～", "再摸摸嘛", "嘿嘿嘿"];
const WAKE_LINES = ["啊…我没睡着！", "嗯？你回来啦"];

const pick = (lines: readonly string[]) => lines[Math.floor(Math.random() * lines.length)];

function clampPosition(p: Point): Point {
  const maxX = window.innerWidth - PET_WIDTH - EDGE;
  const maxY = window.innerHeight - PET_HEIGHT - EDGE;
  return { x: Math.max(EDGE, Math.min(p.x, maxX)), y: Math.max(EDGE, Math.min(p.y, maxY)) };
}

function loadPosition(): Point {
  try {
    const saved = JSON.parse(localStorage.getItem(POSITION_KEY) ?? "null") as Point | null;
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) return clampPosition(saved);
  } catch {
    // 存档损坏就回到默认角落
  }
  return clampPosition({
    x: window.innerWidth - PET_WIDTH - 20,
    y: window.innerHeight - PET_HEIGHT - 20,
  });
}

/** 情绪、眨眼、气泡、粒子与睡眠计时。所有 setState 都在定时器或事件回调里。 */
function usePetMood(thinking: boolean, failed: boolean) {
  const [asleep, setAsleep] = useState(false);
  const [reaction, setReaction] = useState<PetMood | null>(null);
  const [blink, setBlink] = useState(false);
  const [bubble, setBubble] = useState<string | null>(null);
  const [particles, setParticles] = useState<Particle[]>([]);
  const [bounce, setBounce] = useState({ kind: "", n: 0 });
  const timers = useRef({ reaction: 0, bubble: 0, idle: 0 });
  const asleepRef = useRef(false);
  const reactionRef = useRef<PetMood | null>(null);
  const thinkingRef = useRef(thinking);
  const failedRef = useRef(failed);
  const particleId = useRef(0);

  useEffect(() => {
    failedRef.current = failed;
  }, [failed]);

  const react = useCallback((mood: PetMood, ms: number) => {
    window.clearTimeout(timers.current.reaction);
    reactionRef.current = mood;
    setReaction(mood);
    timers.current.reaction = window.setTimeout(() => {
      reactionRef.current = null;
      setReaction(null);
    }, ms);
  }, []);

  const say = useCallback((text: string, ms = 2400) => {
    window.clearTimeout(timers.current.bubble);
    setBubble(text);
    timers.current.bubble = window.setTimeout(() => setBubble(null), ms);
  }, []);

  const burst = useCallback((kind: Particle["kind"], count: number) => {
    const born = Array.from({ length: count }, (_, i) => {
      const angle = -Math.PI / 2 + ((i - (count - 1) / 2) / count) * Math.PI * 1.3;
      const dist = 52 + Math.random() * 30;
      return {
        id: ++particleId.current,
        kind,
        dx: Math.cos(angle) * dist,
        dy: Math.sin(angle) * dist - 10,
        rot: (Math.random() - 0.5) * 240,
        delay: Math.random() * 90,
      };
    });
    setParticles((list) => [...list, ...born]);
    const ids = new Set(born.map((p) => p.id));
    window.setTimeout(() => setParticles((list) => list.filter((p) => !ids.has(p.id))), 1300);
  }, []);

  const scheduleSleep = useCallback(() => {
    window.clearTimeout(timers.current.idle);
    timers.current.idle = window.setTimeout(() => {
      if (thinkingRef.current) return;
      asleepRef.current = true;
      setAsleep(true);
    }, SLEEP_AFTER_MS);
  }, []);

  const wake = useCallback(() => {
    if (asleepRef.current) {
      asleepRef.current = false;
      setAsleep(false);
      react("surprised", 800);
      if (Math.random() < 0.5) say(pick(WAKE_LINES));
    }
    scheduleSleep();
  }, [react, say, scheduleSleep]);

  const idleNormal = () => !reactionRef.current && !asleepRef.current && !thinkingRef.current;

  const actions = {
    wake,
    say,
    flick: () => idleNormal() && react("surprised", 700),
    shake: (text = "晕了晕了…") => {
      react("dizzy", 1700);
      say(text, 1800);
    },
    poke: () => {
      react("happy", 1000);
      burst("flower", 5);
      setBounce((b) => ({ kind: "hop", n: b.n + 1 }));
      if (Math.random() < 0.75) say(pick(POKE_LINES));
    },
    petted: () => {
      react("petted", 1700);
      burst("heart", 3);
      if (Math.random() < 0.6) say(pick(PET_LINES));
    },
    land: () => setBounce((b) => ({ kind: "land", n: b.n + 1 })),
  };

  useEffect(() => {
    let next = 0;
    let reopen = 0;
    const loop = () => {
      next = window.setTimeout(() => {
        if (!reactionRef.current && !asleepRef.current) {
          setBlink(true);
          reopen = window.setTimeout(() => setBlink(false), BLINK_MS);
        }
        loop();
      }, 1800 + Math.random() * 2600);
    };
    loop();
    return () => {
      window.clearTimeout(next);
      window.clearTimeout(reopen);
    };
  }, []);

  useEffect(() => {
    const all = timers.current;
    scheduleSleep();
    const hint = localStorage.getItem(HINT_KEY)
      ? 0
      : window.setTimeout(() => say("双击我可以配置模型哦", 4200), 1500);
    return () => {
      window.clearTimeout(hint);
      Object.values(all).forEach((t) => window.clearTimeout(t));
    };
  }, [say, scheduleSleep]);

  useEffect(() => {
    const was = thinkingRef.current;
    thinkingRef.current = thinking;
    if (thinking === was) return;
    const t = window.setTimeout(() => {
      if (thinking) {
        wake();
        return;
      }
      if (failedRef.current) {
        react("surprised", 1200);
        say("哎呀，出错了…");
        return;
      }
      react("happy", 1400);
      burst("flower", 6);
      say("答好啦～");
    }, thinking ? 0 : 150);
    return () => window.clearTimeout(t);
  }, [thinking, wake, react, burst, say]);

  const mood: PetMood = reaction ?? (thinking ? "thinking" : asleep ? "sleepy" : "normal");
  return { mood, blink, bubble, particles, bounce, actions };
}

interface TrackingHandlers {
  onActivity: () => void;
  onFlick: () => void;
  onShake: () => void;
}

/**
 * 全局指针跟随：身体微微偏向光标、两只瞳孔各自看向光标，拖动时身体摆动。
 * 直接写 CSS 变量，不触发 React 渲染；静止后停掉 rAF。
 */
function usePetTracking(
  rootRef: RefObject<HTMLDivElement | null>,
  posRef: RefObject<Point>,
  draggingRef: RefObject<boolean>,
  swingRef: RefObject<number>,
  handlers: TrackingHandlers,
) {
  const handlersRef = useRef(handlers);
  useEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    const rest = PET_EYES.map((e) => ({ x: e.restX - e.cx, y: e.restY - e.cy }));
    const cur = { fx: 0, fy: 0, tilt: 0, eyes: rest.map((p) => ({ ...p })) };
    const goal = { fx: 0, fy: 0, tilt: 0, eyes: rest.map((p) => ({ ...p })) };
    let pointer: Point | null = null;
    let raf = 0;
    let lastActivity = 0;
    const sample = { t: 0, x: 0, y: 0, dir: 0, flips: [] as number[] };

    const write = () => {
      root.style.setProperty("--fx", `${cur.fx.toFixed(2)}px`);
      root.style.setProperty("--fy", `${cur.fy.toFixed(2)}px`);
      root.style.setProperty("--tilt", `${cur.tilt.toFixed(2)}deg`);
      cur.eyes.forEach((p, i) => {
        root.style.setProperty(`--p${i}x`, `${p.x.toFixed(1)}px`);
        root.style.setProperty(`--p${i}y`, `${p.y.toFixed(1)}px`);
      });
    };

    const aim = () => {
      const { x, y } = posRef.current;
      const dragging = draggingRef.current;
      goal.tilt = dragging ? swingRef.current : 0;
      if (dragging) swingRef.current *= 0.9;
      if (!pointer) return;
      const scale = PET_WIDTH / PET_VIEWBOX.w;
      const dx = pointer.x - (x + PET_WIDTH / 2);
      const dy = pointer.y - (y + PET_HEIGHT * 0.42);
      const dist = Math.hypot(dx, dy) || 1;
      const pull = dragging ? 0 : Math.min(dist * 0.04, 6);
      goal.fx = (dx / dist) * pull;
      goal.fy = (dy / dist) * pull;
      if (!dragging) goal.tilt = Math.max(-1, Math.min(1, dx / 500)) * 4;
      PET_EYES.forEach((e, i) => {
        const ex = pointer!.x - (x + e.cx * scale);
        const ey = pointer!.y - (y + e.cy * scale);
        const d = Math.hypot(ex, ey) || 1;
        const reach = Math.min(d / 160, 1);
        goal.eyes[i] = { x: (ex / d) * reach * (e.rx - 22), y: (ey / d) * reach * (e.ry - 24) };
      });
    };

    const step = () => {
      raf = 0;
      aim();
      let moving = false;
      const lerp = (from: number, to: number) => {
        const next = from + (to - from) * 0.14;
        if (Math.abs(to - next) < 0.05) return to;
        moving = true;
        return next;
      };
      cur.fx = lerp(cur.fx, goal.fx);
      cur.fy = lerp(cur.fy, goal.fy);
      cur.tilt = lerp(cur.tilt, goal.tilt);
      cur.eyes = cur.eyes.map((p, i) => ({ x: lerp(p.x, goal.eyes[i].x), y: lerp(p.y, goal.eyes[i].y) }));
      write();
      if (moving || draggingRef.current) raf = window.requestAnimationFrame(step);
    };
    const kick = () => {
      if (!raf && !document.hidden && !reduce.matches) raf = window.requestAnimationFrame(step);
    };

    const detectGesture = (ev: PointerEvent, now: number) => {
      if (!sample.t) {
        Object.assign(sample, { t: now, x: ev.clientX, y: ev.clientY });
        return;
      }
      const dt = now - sample.t;
      if (dt < 40) return;
      const mx = ev.clientX - sample.x;
      const speed = Math.hypot(mx, ev.clientY - sample.y) / dt;
      const dir = Math.sign(mx);
      sample.flips = sample.flips.filter((t) => now - t <= 1000);
      if (dir && sample.dir && dir !== sample.dir && speed > 2) sample.flips.push(now);
      if (dir) sample.dir = dir;
      Object.assign(sample, { t: now, x: ev.clientX, y: ev.clientY });
      if (draggingRef.current) return;
      if (sample.flips.length > 4) {
        sample.flips = [];
        handlersRef.current.onShake();
      } else if (speed > 6) {
        handlersRef.current.onFlick();
      }
    };

    const onMove = (ev: PointerEvent) => {
      const now = performance.now();
      if (now - lastActivity > 400) {
        lastActivity = now;
        handlersRef.current.onActivity();
      }
      pointer = { x: ev.clientX, y: ev.clientY };
      detectGesture(ev, now);
      kick();
    };
    const onKey = (ev: KeyboardEvent) => {
      handlersRef.current.onActivity();
      const el = ev.target as HTMLElement | null;
      if (el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT")) {
        const r = el.getBoundingClientRect();
        pointer = { x: r.left + r.width * 0.3, y: r.top + r.height / 2 };
        kick();
      }
    };
    const onVisibility = () => {
      root.toggleAttribute("data-paused", document.hidden);
      if (document.hidden && raf) {
        window.cancelAnimationFrame(raf);
        raf = 0;
      }
    };

    write();
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("keydown", onKey);
    document.addEventListener("visibilitychange", onVisibility);
    root.addEventListener("pet:kick", kick);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("visibilitychange", onVisibility);
      root.removeEventListener("pet:kick", kick);
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, [rootRef, posRef, draggingRef, swingRef]);
}

interface PressState {
  x: number;
  y: number;
  originX: number;
  originY: number;
  moved: boolean;
  lastX: number;
  lastT: number;
}

/**
 * 桌宠的全部行为：拖动摆放、单击戳一下、连戳会晕、摸头、双击打开设置。
 *
 * Example:
 *   const pet = useDeskPet({ thinking: busy, onOpenSettings: () => setOpen(true) });
 *   <div ref={pet.rootRef} {...pet.handlers} style={{ left: pet.pos.x, top: pet.pos.y }} />
 */
export function useDeskPet({ thinking, failed, onOpenSettings }: {
  thinking: boolean;
  failed: boolean;
  onOpenSettings: () => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<Point>(() =>
    typeof window === "undefined" ? { x: 0, y: 0 } : loadPosition(),
  );
  const [dragging, setDragging] = useState(false);
  const [pressed, setPressed] = useState(false);
  const posRef = useRef(pos);
  const draggingRef = useRef(false);
  const swingRef = useRef(0);
  const press = useRef<PressState | null>(null);
  const pokes = useRef<number[]>([]);
  const stroke = useRef({ dir: 0, x: 0, t: 0, flips: [] as number[] });
  const { mood, blink, bubble, particles, bounce, actions } = usePetMood(thinking, failed);

  usePetTracking(rootRef, posRef, draggingRef, swingRef, {
    onActivity: actions.wake,
    onFlick: actions.flick,
    onShake: () => actions.shake(),
  });

  useEffect(() => {
    const onResize = () =>
      setPos((p) => {
        const next = clampPosition(p);
        posRef.current = next;
        return next.x === p.x && next.y === p.y ? p : next;
      });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const moveTo = (next: Point) => {
    posRef.current = next;
    setPos(next);
  };

  const detectStroke = (e: ReactPointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    if (e.clientY > rect.top + rect.height * 0.5) return;
    const now = performance.now();
    const s = stroke.current;
    const dt = now - s.t;
    if (dt < 30) return;
    const mx = e.clientX - s.x;
    const speed = Math.abs(mx) / dt;
    const dir = Math.sign(mx);
    s.flips = s.flips.filter((t) => now - t < 1400);
    if (dir && s.dir && dir !== s.dir && speed > 0.05 && speed < 2.5) s.flips.push(now);
    Object.assign(s, { dir: dir || s.dir, x: e.clientX, t: now });
    if (s.flips.length >= 3) {
      s.flips = [];
      actions.wake();
      actions.petted();
    }
  };

  const poke = () => {
    const now = performance.now();
    pokes.current = [...pokes.current.filter((t) => now - t < 1600), now];
    if (pokes.current.length >= 4) {
      pokes.current = [];
      actions.shake("别戳啦，晕了…");
    } else {
      actions.poke();
    }
  };

  const endPress = (e: ReactPointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const p = press.current;
    press.current = null;
    setPressed(false);
    if (!p) return;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    if (p.moved) {
      draggingRef.current = false;
      setDragging(false);
      localStorage.setItem(POSITION_KEY, JSON.stringify(posRef.current));
      actions.land();
      rootRef.current?.dispatchEvent(new Event("pet:kick"));
    } else if (!cancelled) {
      poke();
    }
  };

  const handlers = {
    onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      actions.wake();
      const now = performance.now();
      press.current = {
        x: e.clientX, y: e.clientY, originX: posRef.current.x, originY: posRef.current.y,
        moved: false, lastX: e.clientX, lastT: now,
      };
      e.currentTarget.setPointerCapture?.(e.pointerId);
      setPressed(true);
    },
    onPointerMove: (e: ReactPointerEvent<HTMLDivElement>) => {
      const p = press.current;
      if (!p) {
        detectStroke(e);
        return;
      }
      const dx = e.clientX - p.x;
      const dy = e.clientY - p.y;
      if (!p.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
      if (!p.moved) {
        p.moved = true;
        draggingRef.current = true;
        setDragging(true);
        rootRef.current?.dispatchEvent(new Event("pet:kick"));
      }
      const now = performance.now();
      const vx = (e.clientX - p.lastX) / Math.max(now - p.lastT, 1);
      swingRef.current = Math.max(-22, Math.min(22, swingRef.current * 0.6 + vx * 9));
      Object.assign(p, { lastX: e.clientX, lastT: now });
      moveTo(clampPosition({ x: p.originX + dx, y: p.originY + dy }));
    },
    onPointerUp: (e: ReactPointerEvent<HTMLDivElement>) => endPress(e, false),
    onPointerCancel: (e: ReactPointerEvent<HTMLDivElement>) => endPress(e, true),
    onDoubleClick: () => {
      localStorage.setItem(HINT_KEY, "1");
      onOpenSettings();
    },
    onKeyDown: (e: ReactKeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onOpenSettings();
      }
    },
  };

  return {
    rootRef,
    pos,
    mood: dragging ? ("dragging" as const) : mood,
    blink,
    pressed,
    bubble,
    particles,
    bounce,
    handlers,
  };
}
