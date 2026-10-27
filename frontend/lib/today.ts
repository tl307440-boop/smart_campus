import type { TodayCardData, TodayExam, TodayLesson } from "@/lib/api";

const MINUTE_MS = 60_000;

/** "08:00" → 当天第几分钟；不是时间就返回 null */
export function minutesOf(hhmm: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!match) return null;
  const [hours, minutes] = [Number(match[1]), Number(match[2])];
  return hours < 24 && minutes < 60 ? hours * 60 + minutes : null;
}

/** 学生本机的日期 YYYY-MM-DD；不能用 toISOString，那是 UTC，凌晨会差一天 */
export function localDay(now: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** 某天某个时刻（本机时区）的毫秒时间戳 */
export function atTime(day: string, hhmm: string): number | null {
  const minutes = minutesOf(hhmm);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (minutes === null || !match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Math.floor(minutes / 60), minutes % 60).getTime();
}

export type LessonState = "done" | "now" | "next" | "later";

/**
 * 每节课现在是什么状态：上完了、正在上、马上要上的下一节、之后的。
 * 教务没给节次时间的课算 later，不会冒充「下一节」。
 */
export function lessonStates(lessons: TodayLesson[], now: Date): LessonState[] {
  const current = now.getHours() * 60 + now.getMinutes();
  let nextTaken = false;
  return lessons.map((lesson) => {
    const [start, end] = [minutesOf(lesson.start), minutesOf(lesson.end)];
    if (end !== null && current >= end) return "done";
    if (start !== null && current >= start) return "now";
    if (start !== null && !nextTaken) {
      nextTaken = true;
      return "next";
    }
    return "later";
  });
}

/** 距离某个时刻还有多久，如「35 分钟」「1 小时 20 分钟」；已经过了返回空串 */
export function untilText(hhmm: string, now: Date): string {
  const start = minutesOf(hhmm);
  if (start === null) return "";
  const left = start - (now.getHours() * 60 + now.getMinutes());
  if (left <= 0) return "";
  if (left < 60) return `${left} 分钟`;
  return left % 60 ? `${Math.floor(left / 60)} 小时 ${left % 60} 分钟` : `${left / 60} 小时`;
}

export function examWhen(exam: TodayExam): string {
  const day = exam.days_left === 0 ? "今天" : exam.days_left === 1 ? "明天" : exam.days_left === 2 ? "后天" : `${exam.days_left} 天后`;
  return exam.start ? `${day} ${exam.start}${exam.end ? `–${exam.end}` : ""}` : day;
}

export interface ReminderPlan {
  /** 同一节课在同一天只会有一个，用来给系统通知去重 */
  key: string;
  /** 该提醒的时刻 */
  at: number;
  lesson: TodayLesson;
  lead: number;
}

/**
 * 今天还没到点的提醒。提醒时刻已经过了的不补发：学生中途打开页面，
 * 卡片上已经写着「下一节还有 N 分钟」，再弹一条只会吓人一跳。
 */
export function planReminders(card: TodayCardData, now: Date, lead: number): ReminderPlan[] {
  if (!card.date) return [];
  const plans: ReminderPlan[] = [];
  for (const lesson of card.lessons ?? []) {
    const start = atTime(card.date, lesson.start);
    if (start === null) continue;
    const at = start - lead * MINUTE_MS;
    if (at <= now.getTime()) continue;
    plans.push({ key: `${card.date}-${lesson.start}-${lesson.course}`, at, lesson, lead });
  }
  return plans;
}

export function reminderTitle(plan: ReminderPlan): string {
  return `${plan.lead} 分钟后上课`;
}

export function reminderBody(plan: ReminderPlan): string {
  const { lesson } = plan;
  return [`${lesson.start} ${lesson.course}`, lesson.room].filter(Boolean).join(" · ");
}
