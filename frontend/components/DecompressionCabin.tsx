"use client";

import { useEffect, useRef, useState } from "react";
import { type AmbientSound, type BreathingMode, type CabinState } from "@/lib/api";

const SOUND_OPTIONS: { id: AmbientSound; label: string; desc: string }[] = [
  { id: "none", label: "静音", desc: "安静文字陪伴" },
  { id: "rain", label: "微雨", desc: "舒缓白噪音" },
  { id: "library", label: "自习室", desc: "低频环境底噪" },
  { id: "night_fire", label: "炉火", desc: "温暖低沉助眠" },
  { id: "waves", label: "海浪", desc: "慢速潮汐节律" },
];

const BREATH_PHASES: Record<Exclude<BreathingMode, "none">, { label: string; seconds: number; scale: number }[]> = {
  "4-7-8": [
    { label: "轻柔吸气", seconds: 4, scale: 1.28 },
    { label: "安稳屏息", seconds: 7, scale: 1.28 },
    { label: "缓缓呼气", seconds: 8, scale: 0.88 },
  ],
  box: [
    { label: "吸气", seconds: 4, scale: 1.26 },
    { label: "屏息", seconds: 4, scale: 1.26 },
    { label: "呼气", seconds: 4, scale: 0.88 },
    { label: "放松", seconds: 4, scale: 0.88 },
  ],
};

/** 纯 Web Audio API 合成自然白噪音，不依赖任何外链音频文件 */
class AmbientAudioEngine {
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  private source: AudioBufferSourceNode | null = null;
  private lfo: OscillatorNode | null = null;

  start(kind: AmbientSound) {
    this.stop();
    if (kind === "none" || typeof window === "undefined") return;
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;

    const ctx = new AudioCtx();
    const length = ctx.sampleRate * 4;
    const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
    const data = buffer.getChannelData(0);

    // 粉红/棕噪近似：柔和不刺耳
    let lastOut = 0;
    for (let i = 0; i < length; i++) {
      const white = Math.random() * 2 - 1;
      lastOut = (lastOut + 0.03 * white) / 1.03;
      data[i] = lastOut * (kind === "night_fire" ? 2.6 : 2.1);
    }

    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.loop = true;

    const filter = ctx.createBiquadFilter();
    filter.type = kind === "library" ? "bandpass" : "lowpass";
    filter.frequency.value =
      kind === "rain" ? 1300 : kind === "library" ? 650 : kind === "night_fire" ? 420 : 800;

    const gain = ctx.createGain();
    gain.gain.value = 0.08;

    if (kind === "waves") {
      const lfo = ctx.createOscillator();
      const lfoGain = ctx.createGain();
      lfo.frequency.value = 0.12; // ~8 秒一个浪拍周期
      lfoGain.gain.value = 0.045;
      lfo.connect(lfoGain);
      lfoGain.connect(gain.gain);
      lfo.start();
      this.lfo = lfo;
    }

    src.connect(filter);
    filter.connect(gain);
    gain.connect(ctx.destination);
    src.start();

    this.ctx = ctx;
    this.gain = gain;
    this.source = src;
  }

  stop() {
    try {
      this.lfo?.stop();
    } catch {
      // ignore
    }
    try {
      this.source?.stop();
    } catch {
      // ignore
    }
    if (this.gain) {
      this.gain.disconnect();
    }
    if (this.ctx) {
      void this.ctx.close().catch(() => {});
    }
    this.lfo = null;
    this.source = null;
    this.gain = null;
    this.ctx = null;
  }
}

interface Props {
  cabin: CabinState;
  compact?: boolean;
  onUpdate: (patch: Partial<CabinState>) => void;
}

export function DecompressionCabin({ cabin, compact = false, onUpdate }: Props) {
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [timerRunning, setTimerRunning] = useState(false);
  const [customSeconds, setCustomSeconds] = useState<number | null>(null);
  const [breathTick, setBreathTick] = useState(0);
  const engineRef = useRef<AmbientAudioEngine | null>(null);

  const defaultSeconds = cabin.focus_minutes * 60;
  const secondsLeft = customSeconds ?? defaultSeconds;

  useEffect(() => {
    if (!audioPlaying || cabin.ambient_sound === "none") {
      engineRef.current?.stop();
      return;
    }
    if (!engineRef.current) engineRef.current = new AmbientAudioEngine();
    engineRef.current.start(cabin.ambient_sound);
    return () => {
      engineRef.current?.stop();
    };
  }, [audioPlaying, cabin.ambient_sound]);

  useEffect(() => {
    if (!timerRunning) return;
    const timer = window.setInterval(() => {
      setCustomSeconds((prev) => {
        const cur = prev ?? defaultSeconds;
        if (cur <= 1) {
          setTimerRunning(false);
          return null;
        }
        return cur - 1;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [timerRunning, defaultSeconds]);

  useEffect(() => {
    if (cabin.breathing_mode === "none") return;
    const timer = window.setInterval(() => setBreathTick((t) => t + 1), 1000);
    return () => window.clearInterval(timer);
  }, [cabin.breathing_mode]);

  const activeBreath = cabin.breathing_mode !== "none" ? BREATH_PHASES[cabin.breathing_mode] : null;
  let breathLabel = "";
  let breathSec = 0;
  let breathScale = 1;
  if (activeBreath) {
    const total = activeBreath.reduce((acc, p) => acc + p.seconds, 0);
    let cursor = breathTick % total;
    for (const p of activeBreath) {
      if (cursor < p.seconds) {
        breathLabel = p.label;
        breathSec = p.seconds - cursor;
        breathScale = p.scale;
        break;
      }
      cursor -= p.seconds;
    }
  }

  const mins = String(Math.floor(secondsLeft / 60)).padStart(2, "0");
  const secs = String(secondsLeft % 60).padStart(2, "0");

  function selectSound(sound: AmbientSound) {
    onUpdate({ ambient_sound: sound });
    if (sound === "none") {
      setAudioPlaying(false);
    } else {
      setAudioPlaying(true);
    }
  }

  if (compact) {
    return (
      <div className="sketch bg-skin/70 px-3 py-2.5 text-sm space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-display text-base">🎧 减压伴学舱</span>
          <div className="flex items-center gap-1.5">
            {cabin.ambient_sound !== "none" && (
              <button
                type="button"
                onClick={() => setAudioPlaying((v) => !v)}
                className="btn btn-sm"
              >
                {audioPlaying ? "暂停白噪音" : `播放·${SOUND_OPTIONS.find((s) => s.id === cabin.ambient_sound)?.label}`}
              </button>
            )}
            <button
              type="button"
              onClick={() => onUpdate({ night_mode: !cabin.night_mode })}
              className="btn btn-sm"
            >
              {cabin.night_mode ? "☀️ 日间" : "🌙 树洞"}
            </button>
          </div>
        </div>
        {activeBreath && (
          <div className="flex items-center justify-between rounded bg-paper/70 px-2.5 py-1.5 text-xs">
            <span className="flex items-center gap-2">
              <span
                className="inline-block size-3 rounded-full bg-pink transition-transform duration-700"
                style={{ transform: `scale(${breathScale})` }}
              />
              呼吸引导（{cabin.breathing_mode}）：<strong>{breathLabel}</strong> {breathSec}s
            </span>
            <button type="button" onClick={() => onUpdate({ breathing_mode: "none" })} className="link-ink">
              收起
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="sketch bg-paper p-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-display text-lg">🎧 减压伴学舱</h3>
          <p className="text-xs text-muted">浏览器原生合成环境音 + 呼吸节律引导，亦可由伴侣对话通过 MCP 自动开启</p>
        </div>
        <button
          type="button"
          onClick={() => onUpdate({ night_mode: !cabin.night_mode })}
          className={`btn btn-sm ${cabin.night_mode ? "btn-primary" : ""}`}
        >
          {cabin.night_mode ? "🌙 深夜树洞：已开启" : "☀️ 切换深夜树洞"}
        </button>
      </div>

      {/* 环境白噪音 */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">环境声音</span>
          {cabin.ambient_sound !== "none" && (
            <button type="button" onClick={() => setAudioPlaying((v) => !v)} className="link-ink text-xs">
              {audioPlaying ? "点击静音" : "点击播放（浏览器安全限制需手动点一次）"}
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {SOUND_OPTIONS.map((opt) => {
            const active = cabin.ambient_sound === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => selectSound(opt.id)}
                title={opt.desc}
                className={`btn btn-sm ${active ? "btn-primary" : ""}`}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* 呼吸引导与微专注倒计时 */}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded border border-dashed border-rule bg-skin/40 p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium">呼吸放松节律</span>
            <div className="flex gap-1">
              {(["4-7-8", "box", "none"] as BreathingMode[]).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => onUpdate({ breathing_mode: mode })}
                  className={`rounded px-2 py-0.5 text-xs border border-rule cursor-pointer ${
                    cabin.breathing_mode === mode ? "bg-pink text-ink font-medium" : "bg-paper text-muted"
                  }`}
                >
                  {mode === "none" ? "关闭" : mode}
                </button>
              ))}
            </div>
          </div>
          {activeBreath ? (
            <div className="flex items-center gap-4 pt-1">
              <div className="grid size-14 shrink-0 place-items-center">
                <div
                  className="size-9 rounded-full border-2 border-ink bg-flower/70 transition-transform duration-700"
                  style={{ transform: `scale(${breathScale})` }}
                />
              </div>
              <div>
                <p className="font-display text-lg leading-tight">{breathLabel}</p>
                <p className="text-xs text-muted tabular-nums">本阶段还剩 {breathSec} 秒</p>
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted">焦虑心慌或睡前紧绷时，点击上方「4-7-8」开始着陆呼吸。</p>
          )}
        </div>

        <div className="rounded border border-dashed border-rule bg-skin/40 p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium">陪伴微专注计时</span>
            <span className="text-xs text-muted">{cabin.focus_minutes} 分钟一局</span>
          </div>
          <div className="flex items-center justify-between gap-3 pt-1">
            <span className="font-display text-2xl tabular-nums">
              {mins}:{secs}
            </span>
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => {
                  if (!timerRunning && customSeconds === null) setCustomSeconds(defaultSeconds);
                  setTimerRunning((v) => !v);
                }}
                className="btn btn-sm btn-primary"
              >
                {timerRunning ? "暂停" : "开始"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setTimerRunning(false);
                  setCustomSeconds(null);
                }}
                className="btn btn-sm"
              >
                重置
              </button>
            </div>
          </div>
        </div>
      </div>

      {cabin.note && (
        <p className="text-xs text-muted">伴侣留言：{cabin.note}</p>
      )}
    </div>
  );
}
