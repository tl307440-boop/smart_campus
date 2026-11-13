"use client";

import { useEffect, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import {
  type AcademicSandboxData,
  checkCourseConflicts,
  type ConflictCheckCandidate,
  type ConflictCheckResultItem,
  errorDetail,
  fetchAcademicSandbox,
} from "@/lib/api";

const WEEKDAY_OPTIONS = [
  { value: 1, label: "周一" },
  { value: 2, label: "周二" },
  { value: 3, label: "周三" },
  { value: 4, label: "周四" },
  { value: 5, label: "周五" },
  { value: 6, label: "周六" },
  { value: 7, label: "周日" },
];

const PERIOD_PRESETS = ["1-2节", "3-4节", "5-6节", "7-8节", "9-10节"];

const SLOT_PAIRS = [
  { label: "1-2", nums: [1, 2] },
  { label: "3-4", nums: [3, 4] },
  { label: "5-6", nums: [5, 6] },
  { label: "7-8", nums: [7, 8] },
  { label: "9-10", nums: [9, 10] },
];

interface Props {
  onAsk: (question: string) => void;
}

export function AcademicSandboxPane({ onAsk }: Props) {
  const [data, setData] = useState<AcademicSandboxData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [weekday, setWeekday] = useState(2);
  const [periods, setPeriods] = useState("1-2节");
  const [weeks, setWeeks] = useState("1-16周");
  const [candidates, setCandidates] = useState<ConflictCheckCandidate[]>([]);
  const [results, setResults] = useState<ConflictCheckResultItem[]>([]);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchAcademicSandbox();
        if (!cancelled) setData(res);
      } catch (err) {
        if (!cancelled) setError(errorDetail(err) || "加载学业沙盘失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  async function runConflictCheck(nextList: ConflictCheckCandidate[]) {
    if (nextList.length === 0) {
      setResults([]);
      setCheckError(null);
      return;
    }
    setChecking(true);
    setCheckError(null);
    try {
      const res = await checkCourseConflicts(nextList);
      setResults(res.results);
    } catch (err) {
      setCheckError(errorDetail(err) || "冲突检测失败，请稍后重试");
    } finally {
      setChecking(false);
    }
  }

  function handleAddCandidate(event: React.FormEvent) {
    event.preventDefault();
    const courseName = name.trim() || `试排选修 ${candidates.length + 1}`;
    const nextList: ConflictCheckCandidate[] = [
      ...candidates,
      {
        name: courseName,
        weekday,
        periods: periods.trim() || "1-2节",
        weeks: weeks.trim() || "1-16周",
      },
    ];
    setCandidates(nextList);
    setName("");
    void runConflictCheck(nextList);
  }

  function removeCandidate(index: number) {
    const nextList = candidates.filter((_, i) => i !== index);
    setCandidates(nextList);
    void runConflictCheck(nextList);
  }

  function askAdvisor() {
    if (!data) return;
    const missingCats = (data.categories ?? [])
      .filter((c) => c.missing > 0)
      .map((c) => `${c.name}差 ${c.missing} 分`);
    const freeHints = (data.free_windows ?? []).slice(0, 4).join("、");
    const parts = [
      data.summary && data.summary.total_missing > 0
        ? `我目前毕业共还差 ${data.summary.total_missing} 学分（${missingCats.join("，")}）`
        : "我目前修读进度已录入教务系统",
      freeHints ? `当前课表空闲时段有：${freeHints}` : "",
      "请结合本校培养方案帮我规划选课和补学分建议",
    ].filter(Boolean);
    onAsk(`${parts.join("；")}。`);
  }

  if (loading) {
    return (
      <div>
        <PaneTitle title="学业规划沙盘">查看毕业学分缺口、每周空闲时段并试排选课冲突</PaneTitle>
        <p className="py-10 text-center text-sm text-muted">正在拉取教务学业与课表数据…</p>
      </div>
    );
  }

  if (error || !data?.bound) {
    return (
      <div>
        <PaneTitle title="学业规划沙盘">查看毕业学分缺口、每周空闲时段并试排选课冲突</PaneTitle>
        <div className="sketch bg-paper p-5 text-sm text-muted">
          {error || "请先在「教务账号」完成绑定，才能生成你的毕业学分沙盘和选课冲突检测。"}
        </div>
      </div>
    );
  }

  const summary = data.summary;
  const categories = data.categories ?? [];
  const failedCourses = data.failed_courses ?? [];
  const dailyLoad = data.daily_load ?? [];
  const freeWindows = data.free_windows ?? [];
  const showWeekend = dailyLoad.some((d) => d.weekday >= 6 && d.course_count > 0);
  const visibleDays = dailyLoad.filter((d) => d.weekday <= 5 || showWeekend);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <PaneTitle title="学业规划沙盘">
          {data.term} · {data.current_week ? `第 ${data.current_week} 周` : "当前学期"}
        </PaneTitle>
        <button type="button" onClick={askAdvisor} className="btn btn-sm btn-primary">
          让助手结合培养方案推荐选课
        </button>
      </div>

      {/* 1. 毕业学分缺口概览 */}
      <section className="sketch space-y-4 bg-paper p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h3 className="font-display text-lg">毕业学分进度</h3>
            <p className="text-xs text-muted">
              {summary && summary.total_required > 0
                ? `已获 ${summary.total_earned} / ${summary.total_required} 学分，还差 ${summary.total_missing} 学分`
                : "分类学分要求以教务学业情况为准"}
            </p>
          </div>
          <div className="text-right">
            <span className="font-display text-2xl">{summary?.completion_pct ?? 0}%</span>
            {data.gpa != null && <div className="text-xs text-muted">平均绩点 {data.gpa.toFixed(2)}</div>}
          </div>
        </div>

        {summary && summary.total_required > 0 && (
          <div className="h-3 w-full overflow-hidden rounded-full border border-ink/30 bg-desk">
            <div
              className="h-full bg-pink transition-all duration-300"
              style={{ width: `${summary.completion_pct}%` }}
            />
          </div>
        )}

        {data.plan && (
          <div className="grid grid-cols-4 gap-2 text-center text-xs">
            <div className="rounded-lg border border-rule bg-desk/70 px-2 py-2">
              <div className="text-muted">已通过</div>
              <div className="mt-0.5 text-base font-bold">{data.plan.passed}</div>
            </div>
            <div className="rounded-lg border border-rule bg-desk/70 px-2 py-2">
              <div className="text-muted">在读</div>
              <div className="mt-0.5 text-base font-bold">{data.plan.in_progress}</div>
            </div>
            <div className="rounded-lg border border-rule bg-desk/70 px-2 py-2">
              <div className="text-muted">未修</div>
              <div className="mt-0.5 text-base font-bold">{data.plan.not_taken}</div>
            </div>
            <div className={`rounded-lg border px-2 py-2 ${data.plan.failed > 0 ? "border-danger/40 bg-skin" : "border-rule bg-desk/70"}`}>
              <div className={data.plan.failed > 0 ? "text-danger" : "text-muted"}>未通过</div>
              <div className={`mt-0.5 text-base font-bold ${data.plan.failed > 0 ? "text-danger" : ""}`}>{data.plan.failed}</div>
            </div>
          </div>
        )}

        {categories.length > 0 && (
          <div className="space-y-2.5 pt-1">
            {categories.map((cat) => (
              <div key={cat.name} className="space-y-1 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-ink">{cat.name}</span>
                  <span className="text-muted">
                    {cat.earned} / {cat.required} 分
                    {cat.missing > 0 ? (
                      <span className="ml-1.5 font-semibold text-pink">差 {cat.missing}</span>
                    ) : (
                      <span className="ml-1.5 font-semibold text-ink">已修满</span>
                    )}
                  </span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-desk">
                  <div
                    className={`h-full transition-all ${
                      cat.status === "done" ? "bg-flower" : cat.status === "almost" ? "bg-pollen" : "bg-pink"
                    }`}
                    style={{ width: `${cat.pct}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}

        {failedCourses.length > 0 && (
          <div className="rounded-xl border border-danger/35 bg-skin/70 p-3 text-xs">
            <div className="font-semibold text-danger">需优先关注的重修 / 补考课程</div>
            <ul className="mt-1.5 space-y-1 text-ink">
              {failedCourses.map((fc) => (
                <li key={`${fc.term}-${fc.course}`} className="flex justify-between gap-2">
                  <span>{fc.course}</span>
                  <span className="text-muted">
                    {fc.term} · 成绩 {fc.score}（{fc.credit}学分）
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* 2. 周课表空闲窗口 */}
      <section className="sketch space-y-3 bg-paper p-4">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="font-display text-lg">本学期课表占用与空闲时段</h3>
          <span className="text-xs text-muted">粉色为已排课</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-center text-xs">
            <thead>
              <tr className="text-muted">
                <th className="py-1 pr-2 text-left font-normal">节次</th>
                {visibleDays.map((day) => (
                  <th key={day.weekday} className="px-1 py-1 font-medium">
                    {day.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {SLOT_PAIRS.map((slot) => (
                <tr key={slot.label}>
                  <td className="py-1 pr-2 text-left text-muted">{slot.label}节</td>
                  {visibleDays.map((day) => {
                    const hit = day.courses.filter((c) =>
                      c.period_nums.some((p) => slot.nums.includes(p))
                    );
                    const occupied = hit.length > 0;
                    return (
                      <td key={`${day.weekday}-${slot.label}`} className="p-0.5">
                        <div
                          title={occupied ? hit.map((c) => `${c.course} (${c.weeks})`).join(" / ") : "空闲"}
                          className={`truncate rounded px-1 py-1.5 ${
                            occupied ? "border border-ink/20 bg-skin font-medium text-ink" : "bg-desk/70 text-muted/60"
                          }`}
                        >
                          {occupied ? hit[0].course.replace(/（演示）$/, "").slice(0, 4) : "空"}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {freeWindows.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {freeWindows.slice(0, 6).map((win) => (
              <span key={win} className="rounded-full border border-rule bg-desk px-2.5 py-0.5 text-xs text-ink">
                {win}
              </span>
            ))}
          </div>
        )}
      </section>

      {/* 3. 交互式选课冲突试排器 */}
      <section className="sketch space-y-3 bg-paper p-4">
        <div>
          <h3 className="font-display text-lg">选课冲突试排沙盘</h3>
          <p className="text-xs text-muted">填几门想选的通识/重修课，按单双周与节次精确查撞课（不耗 AI 额度）</p>
        </div>

        <form onSubmit={handleAddCandidate} className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <label className="col-span-2 sm:col-span-4">
            <span className="field-label">拟选课程名</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：心理学导论 / 影视鉴赏"
              className="field text-sm"
            />
          </label>

          <label>
            <span className="field-label">星期</span>
            <select
              value={weekday}
              onChange={(e) => setWeekday(Number(e.target.value))}
              className="field text-sm"
            >
              {WEEKDAY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>

          <label>
            <span className="field-label">节次</span>
            <select
              value={periods}
              onChange={(e) => setPeriods(e.target.value)}
              className="field text-sm"
            >
              {PERIOD_PRESETS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>

          <label className="col-span-2">
            <span className="field-label">教学周次（支持单双周）</span>
            <input
              type="text"
              value={weeks}
              onChange={(e) => setWeeks(e.target.value)}
              placeholder="1-16周 或 2-16周(双)"
              className="field text-sm"
            />
          </label>

          <div className="col-span-2 flex justify-end sm:col-span-4">
            <button type="submit" disabled={checking} className="btn btn-sm btn-primary">
              {checking ? "检测中…" : "+ 加入试排并检测冲突"}
            </button>
          </div>
        </form>

        {checkError && <p className="text-xs text-danger">{checkError}</p>}

        {results.length > 0 && (
          <ul className="space-y-2 pt-1">
            {results.map((item, idx) => (
              <li
                key={`${item.name}-${idx}`}
                className={`rounded-xl border p-3 text-xs ${
                  item.conflict ? "border-danger/45 bg-skin/65" : "border-rule bg-desk/50"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <span className="font-semibold text-ink">{item.name}</span>
                    <span className={`ml-2 rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                      item.conflict ? "bg-danger/15 text-danger" : "bg-flower/45 text-ink"
                    }`}>
                      {item.conflict ? "有冲突" : "可排入"}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeCandidate(idx)}
                    className="btn-danger text-xs underline-offset-2 hover:underline"
                  >
                    移除
                  </button>
                </div>
                <p className="mt-1 text-muted">{item.summary}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
