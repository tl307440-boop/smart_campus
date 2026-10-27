"use client";

/** 课前提醒的偏好，只存在这台浏览器里：开没开、提前几分钟。 */
import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "zhiyue.reminder";
const CHANGE_EVENT = "zhiyue:reminder-settings";

export const LEAD_CHOICES = [10, 20, 30] as const;

export interface ReminderSettings {
  enabled: boolean;
  /** 提前几分钟，取值只会是 LEAD_CHOICES 里的 */
  lead: number;
}

const DEFAULT_REMINDER: ReminderSettings = { enabled: false, lead: 20 };

let cachedRaw: string | null = null;
let cachedValue: ReminderSettings = DEFAULT_REMINDER;

function readSnapshot(): ReminderSettings {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === cachedRaw) return cachedValue;
  cachedRaw = raw;
  try {
    const parsed = raw ? (JSON.parse(raw) as Partial<ReminderSettings>) : {};
    const lead = LEAD_CHOICES.find((choice) => choice === parsed.lead);
    cachedValue = { enabled: parsed.enabled === true, lead: lead ?? DEFAULT_REMINDER.lead };
  } catch {
    cachedValue = DEFAULT_REMINDER;
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

export function useReminderSettings(): [ReminderSettings, (patch: Partial<ReminderSettings>) => void] {
  const settings = useSyncExternalStore(subscribe, readSnapshot, () => DEFAULT_REMINDER);
  const update = useCallback((patch: Partial<ReminderSettings>) => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...readSnapshot(), ...patch }));
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);
  return [settings, update];
}

/** 浏览器能不能弹系统通知，以及学生是否已经点过拒绝；不支持的浏览器照样能用页面内提醒 */
export type NotifyAccess = "granted" | "denied" | "default" | "unsupported";

export function notifyAccess(): NotifyAccess {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

/** 必须在点击里调用，否则浏览器不给弹授权框 */
export async function askNotifyAccess(): Promise<NotifyAccess> {
  if (typeof Notification === "undefined") return "unsupported";
  if (Notification.permission !== "default") return Notification.permission;
  try {
    return await Notification.requestPermission();
  } catch {
    return notifyAccess();
  }
}
