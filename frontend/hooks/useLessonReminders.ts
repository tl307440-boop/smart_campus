"use client";

import { useEffect } from "react";
import type { TodayCardData } from "@/lib/api";
import { type ReminderSettings } from "@/lib/reminder-settings";
import { planReminders, type ReminderPlan, reminderBody, reminderTitle } from "@/lib/today";

/**
 * 每节课前弹一次提醒。页面开着且在前台时用页面内的提示；切到别的标签页时，
 * 学生授权过就弹系统通知。页面关了就没有提醒，这里不假装能做到。
 */
export function useLessonReminders(
  card: TodayCardData | null,
  settings: ReminderSettings,
  notify: (text: string) => void,
): void {
  const { enabled, lead } = settings;

  useEffect(() => {
    if (!enabled || !card) return;

    const fire = (plan: ReminderPlan) => {
      if (document.visibilityState === "visible") {
        notify(`${reminderTitle(plan)}：${reminderBody(plan)}`);
      } else if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        new Notification(reminderTitle(plan), { body: reminderBody(plan), tag: plan.key });
      }
    };

    const now = Date.now();
    const timers = planReminders(card, new Date(now), lead).map((plan) => window.setTimeout(() => fire(plan), plan.at - now));
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [card, enabled, lead, notify]);
}
