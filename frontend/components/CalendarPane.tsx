"use client";

import { useEffect, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import { useToast } from "@/components/Toast";
import {
  CALENDAR_ALARMS,
  type CalendarAlarm,
  type CalendarSubscription,
  type NewCalendarSubscription,
  calendarFeedUrl,
  createCalendarSubscription,
  deleteCalendarSubscription,
  downloadCalendar,
  errorDetail,
  fetchCalendarSubscription,
} from "@/lib/api";

const DEFAULT_ALARM: CalendarAlarm = 15;
/** 后端要学生手填开学周一时，报错里一定有这句 */
const NEEDS_TERM_START = "第一周的周一";

function alarmLabel(minutes: CalendarAlarm): string {
  return minutes === 0 ? "不提醒" : `课前 ${minutes} 分钟`;
}

function AlarmSelect({ value, onChange, id }: { value: CalendarAlarm; onChange: (next: CalendarAlarm) => void; id: string }) {
  return (
    <select
      id={id}
      value={value}
      onChange={(event) => onChange(Number(event.target.value) as CalendarAlarm)}
      className="field w-auto py-1"
    >
      {CALENDAR_ALARMS.map((minutes) => (
        <option key={minutes} value={minutes}>
          {alarmLabel(minutes)}
        </option>
      ))}
    </select>
  );
}

/** 本机地址手机访问不到：开发时常见，提示一句免得学生白忙 */
function isLocalHost(url: string): boolean {
  try {
    const host = new URL(url).hostname;
    return host === "localhost" || host === "127.0.0.1" || host === "::1" || host.endsWith(".local");
  } catch {
    return false;
  }
}

function ExportSection() {
  const toast = useToast();
  const [alarm, setAlarm] = useState<CalendarAlarm>(DEFAULT_ALARM);
  const [termStart, setTermStart] = useState("");
  const [needTermStart, setNeedTermStart] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      await downloadCalendar(alarm, needTermStart && termStart ? termStart : undefined);
      toast.success("课表已下载，用手机或电脑的日历打开它就能导入");
    } catch (err) {
      const message = errorDetail(err).slice(0, 160);
      setNeedTermStart(message.includes(NEEDS_TERM_START));
      setError(message || "导出失败，稍后再试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h3 className="font-display text-lg">下载课表文件</h3>
      <p className="mt-1 text-sm text-muted">本学期每一节课和考试都会变成日历里的一个日程，带教室和老师。调课后要重新下载，想自动更新用下面的订阅。</p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <label htmlFor="cal-alarm" className="text-sm text-muted">
          提醒
        </label>
        <AlarmSelect id="cal-alarm" value={alarm} onChange={setAlarm} />
        <button type="button" onClick={() => void download()} disabled={busy || (needTermStart && !termStart)} className="btn btn-sm btn-primary">
          {busy ? "正在生成…" : "下载 .ics"}
        </button>
      </div>
      {needTermStart && (
        <div className="rise-in mt-3">
          <label htmlFor="cal-start" className="field-label">
            开学第一周的周一
          </label>
          <input id="cal-start" type="date" value={termStart} onChange={(event) => setTermStart(event.target.value)} className="field mt-1 w-auto" />
        </div>
      )}
      {error && (
        <p className="mt-2 text-[0.9375rem] break-all text-danger" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function SubscribeSection() {
  const toast = useToast();
  const [status, setStatus] = useState<CalendarSubscription | null>(null);
  const [fresh, setFresh] = useState<NewCalendarSubscription | null>(null);
  const [alarm, setAlarm] = useState<CalendarAlarm>(DEFAULT_ALARM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await fetchCalendarSubscription();
        if (cancelled) return;
        setStatus(next);
        if (next.alarm !== undefined) setAlarm(next.alarm);
      } catch (err) {
        if (!cancelled) setError(errorDetail(err).slice(0, 120) || "没读到订阅状态");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const created = await createCalendarSubscription(alarm);
      setFresh(created);
      setStatus({ active: true, alarm: created.alarm });
    } catch (err) {
      setError(errorDetail(err).slice(0, 120) || "生成失败，稍后再试");
    } finally {
      setBusy(false);
    }
  }

  async function turnOff() {
    setBusy(true);
    setError(null);
    try {
      await deleteCalendarSubscription();
      setStatus({ active: false });
      setFresh(null);
      toast.success("订阅已关闭，旧链接不能用了");
    } catch (err) {
      setError(errorDetail(err).slice(0, 120) || "关闭失败，稍后再试");
    } finally {
      setBusy(false);
    }
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      toast.success("链接已复制");
    } catch {
      toast.error("没能复制，长按链接手动复制吧");
    }
  }

  const url = fresh ? calendarFeedUrl(fresh.path) : "";

  return (
    <div className="mt-8">
      <h3 className="font-display text-lg">订阅课表（调课后自动更新）</h3>
      <p className="mt-1 text-sm text-muted">在手机日历里「添加订阅日历」，填下面的链接。手机会隔一段时间自己来取最新的课表，不用再重新导入。</p>

      {status?.active && !fresh && (
        <p className="mt-3 text-[0.9375rem]">已开启订阅，{alarmLabel(status.alarm ?? DEFAULT_ALARM)}。链接只在生成时显示一次，想换一条或者弄丢了，重新生成就行。</p>
      )}

      {fresh && (
        <div className="rise-in mt-3">
          <label htmlFor="cal-url" className="field-label">
            订阅链接（只显示这一次）
          </label>
          <div className="mt-1 flex gap-2">
            <input id="cal-url" readOnly value={url} onFocus={(event) => event.currentTarget.select()} className="field min-w-0 flex-1 text-sm" />
            <button type="button" onClick={() => void copy(url)} className="btn btn-sm shrink-0">
              复制
            </button>
          </div>
          <p className="mt-2 text-sm text-muted">
            苹果手机可以
            <a href={url.replace(/^https?:/, "webcal:")} className="link-ink mx-1">
              点这里直接订阅
            </a>
            ；安卓用 Google 日历或系统日历的「通过网址添加」。
          </p>
          {isLocalHost(url) && (
            <p className="mt-1 text-sm text-danger">这条链接指向本机地址，手机访问不到。要让学校的后端有公网地址，手机才能订阅。</p>
          )}
          <p className="mt-1 text-sm text-muted">这条链接就是钥匙，拿到的人都能看到你的课表，别发给别人。</p>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <label htmlFor="cal-sub-alarm" className="text-sm text-muted">
          提醒
        </label>
        <AlarmSelect id="cal-sub-alarm" value={alarm} onChange={setAlarm} />
        <button type="button" onClick={() => void generate()} disabled={busy || status === null} className="btn btn-sm btn-primary">
          {status?.active ? "重新生成链接" : "生成订阅链接"}
        </button>
        {status?.active && (
          <button type="button" onClick={() => void turnOff()} disabled={busy} className="link-ink text-sm">
            关闭订阅
          </button>
        )}
      </div>
      {error && (
        <p className="mt-2 text-[0.9375rem] break-all text-danger" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** 抽屉页「日历」：把本学期的课表和考试放进手机日历，一次性下载，或订阅后自动更新。 */
export function CalendarPane() {
  return (
    <section>
      <PaneTitle title="日历">先在「教务账号」里绑定，再把课表放进手机日历，上课、考试不用自己记。</PaneTitle>
      <ExportSection />
      <SubscribeSection />
    </section>
  );
}
