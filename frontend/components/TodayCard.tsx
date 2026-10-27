"use client";

import { useEffect, useState } from "react";
import { CheckIcon } from "@/components/SketchIcons";
import type { TodayState } from "@/hooks/useToday";
import type { TodayCardData, TodayLesson } from "@/lib/api";
import { askNotifyAccess, LEAD_CHOICES, type NotifyAccess, notifyAccess, type ReminderSettings } from "@/lib/reminder-settings";
import { examWhen, type LessonState, lessonStates, untilText } from "@/lib/today";

const CLOCK_MS = 30_000;

interface Props {
  state: TodayState;
  reminder: ReminderSettings;
  onReminderChange: (patch: Partial<ReminderSettings>) => void;
  onRetry: () => void;
  /** 打开「教务账号」绑定页 */
  onBind: () => void;
}

/** 每半分钟走一格，用来判断哪节课在上、哪节是下一节 */
function useClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), CLOCK_MS);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function LessonRow({ lesson, state, now }: { lesson: TodayLesson; state: LessonState; now: Date }) {
  const where = [lesson.room, lesson.teacher].filter(Boolean).join(" · ");
  const until = state === "next" ? untilText(lesson.start, now) : "";
  return (
    <li className={`flex gap-3 py-1.5 ${state === "done" ? "opacity-50" : ""}`}>
      <div className="w-12 shrink-0 pt-0.5 text-sm leading-5 tabular-nums text-muted">
        {lesson.start ? (
          <>
            <div>{lesson.start}</div>
            <div className="opacity-70">{lesson.end}</div>
          </>
        ) : (
          lesson.periods
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="font-display text-lg leading-snug">
          <span className={state === "now" ? "marker" : ""}>{lesson.course}</span>
        </p>
        {where && <p className="text-sm text-muted">{where}</p>}
      </div>
      {state === "now" && <span className="h-fit shrink-0 rounded-full border border-ink bg-pollen px-2 text-xs leading-5">上课中</span>}
      {state === "next" && until && (
        <span className="h-fit shrink-0 rounded-full border border-ink bg-skin px-2 text-xs leading-5">{until}后</span>
      )}
      {state === "done" && <CheckIcon size={16} className="mt-1 shrink-0 text-muted" />}
    </li>
  );
}

function ReminderControl({ reminder, onChange }: { reminder: ReminderSettings; onChange: (patch: Partial<ReminderSettings>) => void }) {
  const [access, setAccess] = useState<NotifyAccess>(() => notifyAccess());

  async function toggle() {
    if (reminder.enabled) {
      onChange({ enabled: false });
      return;
    }
    onChange({ enabled: true });
    setAccess(await askNotifyAccess());
  }

  function nextLead() {
    const at = LEAD_CHOICES.findIndex((choice) => choice === reminder.lead);
    onChange({ lead: LEAD_CHOICES[(at + 1) % LEAD_CHOICES.length] });
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t-2 border-dashed border-rule pt-2.5">
      <button type="button" role="switch" aria-checked={reminder.enabled} onClick={() => void toggle()} className="btn btn-sm">
        {reminder.enabled ? "课前提醒：开" : "开启课前提醒"}
      </button>
      {reminder.enabled && (
        <button type="button" onClick={nextLead} className="link-ink text-sm" title="点一下换个时间">
          提前 {reminder.lead} 分钟
        </button>
      )}
      <p className="min-w-0 flex-1 text-xs leading-snug text-muted">
        {!reminder.enabled
          ? "开了之后，上课前我会叫你一声。"
          : access === "granted"
            ? "这个页面开着就会提醒，切到别的标签页也会弹通知。"
            : "这个页面开着时会提醒；浏览器通知没打开，切走就看不到。"}
      </p>
    </div>
  );
}

function ReadyCard({ card, ...controls }: { card: TodayCardData } & Pick<Props, "reminder" | "onReminderChange">) {
  const now = useClock();
  const lessons = card.lessons ?? [];
  const exams = card.exams ?? [];
  const states = lessonStates(lessons, now);
  const allDone = lessons.length > 0 && states.every((state) => state === "done");

  return (
    <section className="sketch rise-in bg-paper px-4 py-3.5" aria-label="今天的安排">
      <header className="flex items-baseline justify-between gap-3">
        <h2 className="font-display text-xl leading-tight">今天 · {card.weekday}</h2>
        {card.week ? <span className="text-sm text-muted">第 {card.week} 周</span> : null}
      </header>

      {lessons.length > 0 ? (
        <ul className="mt-1.5">
          {lessons.map((lesson, i) => (
            <LessonRow key={`${lesson.start}-${lesson.course}`} lesson={lesson} state={states[i]} now={now} />
          ))}
        </ul>
      ) : (
        <p className="mt-1.5 text-muted">今天没有课，自己安排吧。</p>
      )}
      {allDone && <p className="mt-1 text-sm text-muted">今天的课都上完啦，辛苦了。</p>}
      {card.week_unknown && lessons.length > 0 && (
        <p className="mt-1 text-xs text-muted">教务没给出现在是第几周，上面没按周次筛，以教务系统为准。</p>
      )}

      {exams.length > 0 && (
        <div className="mt-3 border-t-2 border-dashed border-rule pt-2.5">
          <p className="text-sm text-muted">快到的考试</p>
          <ul className="mt-1">
            {exams.map((exam) => (
              <li key={`${exam.date}-${exam.course}`} className="flex flex-wrap items-baseline justify-between gap-x-3 py-1">
                <span className="font-display text-lg leading-snug">{exam.course}</span>
                <span className="text-sm text-muted">
                  {[examWhen(exam), exam.room, exam.seat ? `座位 ${exam.seat}` : ""].filter(Boolean).join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {lessons.length > 0 && <ReminderControl reminder={controls.reminder} onChange={controls.onReminderChange} />}
    </section>
  );
}

/** 空白页上的今日卡片：今天的课、快到的考试、课前提醒开关。学校没开教务时什么都不画。 */
export function TodayCard({ state, reminder, onReminderChange, onRetry, onBind }: Props) {
  if (state.status === "loading") return <p className="text-sm text-muted">正在看今天有什么课…</p>;

  if (state.status === "error") {
    return (
      <p className="text-sm text-muted">
        今天的课暂时没查到。
        <button type="button" onClick={onRetry} className="link-ink ml-1">
          再试一次
        </button>
      </p>
    );
  }

  const { card } = state;
  if (!card.enabled) return null;

  if (!card.bound) {
    return (
      <section className="sketch rise-in flex flex-wrap items-center justify-between gap-3 bg-paper px-4 py-3.5">
        <p className="min-w-0 flex-1 text-[0.9375rem] leading-snug">绑定教务账号后，这里会摆出你今天的课和快到的考试。</p>
        <button type="button" onClick={onBind} className="btn btn-sm btn-primary">
          去绑定
        </button>
      </section>
    );
  }

  return <ReadyCard card={card} reminder={reminder} onReminderChange={onReminderChange} />;
}
