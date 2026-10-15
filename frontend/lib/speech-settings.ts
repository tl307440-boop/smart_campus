"use client";

/** 朗读回答的偏好，只存在这台浏览器里。声音来自浏览器自带的 speechSynthesis，不经过后端、不花钱。 */
import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "zhiyue.speech";
const CHANGE_EVENT = "zhiyue:speech-settings";

export const RATE_RANGE = [0.6, 1.6] as const;

export interface SpeechSettings {
  enabled: boolean;
  /** 空串表示让浏览器挑默认的中文声音 */
  voiceURI: string;
  rate: number;
}

const DEFAULT_SPEECH: SpeechSettings = { enabled: false, voiceURI: "", rate: 1 };

let cachedRaw: string | null = null;
let cachedValue: SpeechSettings = DEFAULT_SPEECH;

function readSnapshot(): SpeechSettings {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === cachedRaw) return cachedValue;
  cachedRaw = raw;
  try {
    const parsed = raw ? (JSON.parse(raw) as Partial<SpeechSettings>) : {};
    const rate = Number(parsed.rate);
    cachedValue = {
      enabled: parsed.enabled === true,
      voiceURI: typeof parsed.voiceURI === "string" ? parsed.voiceURI : "",
      rate: Number.isFinite(rate) ? Math.min(RATE_RANGE[1], Math.max(RATE_RANGE[0], rate)) : 1,
    };
  } catch {
    cachedValue = DEFAULT_SPEECH;
  }
  return cachedValue;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** 页面和「AI 服务」页各自读写同一份偏好，改一处另一处立刻跟上 */
export function useSpeechSettings(): [SpeechSettings, (patch: Partial<SpeechSettings>) => void] {
  const settings = useSyncExternalStore(subscribe, readSnapshot, () => DEFAULT_SPEECH);
  const update = useCallback((patch: Partial<SpeechSettings>) => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...readSnapshot(), ...patch }));
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);
  return [settings, update];
}

const NO_VOICES: SpeechSynthesisVoice[] = [];
let cachedVoiceKey = "";
let cachedVoices = NO_VOICES;

function voicesSnapshot(): SpeechSynthesisVoice[] {
  const list = window.speechSynthesis?.getVoices().filter((voice) => voice.lang.toLowerCase().startsWith("zh")) ?? [];
  const key = list.map((voice) => voice.voiceURI).join("|");
  if (key !== cachedVoiceKey) {
    cachedVoiceKey = key;
    cachedVoices = list;
  }
  return cachedVoices;
}

/** Chrome 的声音列表是异步加载的，要等 voiceschanged */
function subscribeVoices(onChange: () => void): () => void {
  const synth = window.speechSynthesis;
  if (!synth) return () => {};
  synth.addEventListener("voiceschanged", onChange);
  return () => synth.removeEventListener("voiceschanged", onChange);
}

const noSubscription = () => () => {};

/** 浏览器里可用的中文声音；不支持朗读的浏览器 supported 为 false */
export function useChineseVoices(): { supported: boolean; voices: SpeechSynthesisVoice[] } {
  const voices = useSyncExternalStore(subscribeVoices, voicesSnapshot, () => NO_VOICES);
  const supported = useSyncExternalStore(noSubscription, () => "speechSynthesis" in window, () => true);
  return { supported, voices };
}

/** 返回这次朗读，调用方要在念完时改按钮状态可以监听它的 end；浏览器不支持时返回 null */
export function speakText(text: string, settings: SpeechSettings): SpeechSynthesisUtterance | null {
  const synth = window.speechSynthesis;
  if (!synth) return null;
  synth.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "zh-CN";
  utterance.rate = settings.rate;
  const voice = settings.voiceURI ? synth.getVoices().find((item) => item.voiceURI === settings.voiceURI) : undefined;
  if (voice) utterance.voice = voice;
  synth.speak(utterance);
  return utterance;
}

/** 念出来之前去掉 Markdown 记号，免得把「星号星号」读出来；链接只念文字 */
export function speakableText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/^\s*\|?[\s:|-]+\|?\s*$/gm, "")
    .replace(/[*_`~#>|]/g, "")
    .replace(/\n{2,}/g, "\n")
    .trim();
}
