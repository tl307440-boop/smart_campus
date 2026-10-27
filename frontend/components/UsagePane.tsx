"use client";

import { useEffect, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import { useToast } from "@/components/Toast";
import {
  type UsageDays,
  type UsageGap,
  type UsageReport,
  errorDetail,
  fetchUsage,
  uploadDocument,
} from "@/lib/api";

const PERIODS: { days: UsageDays; label: string }[] = [
  { days: 7, label: "近 7 天" },
  { days: 30, label: "近 30 天" },
  { days: 90, label: "近 90 天" },
];

const KIND_LABEL = { refused: "拒答", no_source: "没找到资料", downvoted: "被点踩" } as const;
const REASON_LABEL = { wrong: "答非所问", outdated: "信息过期", verbose: "太啰嗦", other: "其他" } as const;
/** 工具失败率到这个线就标红，提醒管理员去看看是不是教务或第三方接口出了问题 */
const FAILURE_ALERT = 0.3;

function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function seconds(ms: number): string {
  return ms < 1000 ? `${ms} 毫秒` : `${(ms / 1000).toFixed(1)} 秒`;
}

/** 2026-10-27T09:30:00 → 10/27 09:30 */
function clock(iso: string): string {
  const match = /^\d{4}-(\d{2})-(\d{2})T(\d{2}:\d{2})/.exec(iso);
  return match ? `${match[1]}/${match[2]} ${match[3]}` : "";
}

/** 补进知识库的文件名：能一眼认出是哪个问题，又不含系统不让用的字符 */
function noteFileName(question: string): string {
  const short = question.replace(/[\\/:*?"<>|\s]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24);
  return `补充问答-${short || "未命名"}.md`;
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="sketch bg-paper px-3.5 py-3">
      <p className="text-sm text-muted">{label}</p>
      <p className="font-display text-[1.75rem] leading-tight tabular-nums">{value}</p>
      {note && <p className="text-[0.8125rem] text-muted tabular-nums">{note}</p>}
    </div>
  );
}

function GapItem({ gap, added, onAdded }: { gap: UsageGap; added: boolean; onAdded: (question: string) => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [answer, setAnswer] = useState("");
  const [saving, setSaving] = useState(false);
  const kinds = Object.entries(gap.kinds) as [keyof typeof KIND_LABEL, number][];
  const reasons = Object.entries(gap.reasons) as [keyof typeof REASON_LABEL, number][];
  const text = answer.trim();

  async function save() {
    setSaving(true);
    try {
      const file = new File([`问：${gap.question}\n\n答：${text}\n`], noteFileName(gap.question), { type: "text/markdown" });
      await uploadDocument(file);
      toast.success("已补进知识库，建好索引后就能答了");
      setEditing(false);
      onAdded(gap.question);
    } catch (err) {
      toast.error(`没补上：${errorDetail(err).slice(0, 80)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="border-b border-dashed border-rule py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-words">{gap.question}</p>
          <p className="mt-0.5 flex flex-wrap gap-x-2 text-[0.8125rem] text-muted">
            <span className="tabular-nums">{gap.count} 次</span>
            {kinds.map(([kind, n]) => (
              <span key={kind}>
                {KIND_LABEL[kind]}
                {n > 1 ? ` ×${n}` : ""}
              </span>
            ))}
            {reasons.map(([reason, n]) => (
              <span key={reason}>
                「{REASON_LABEL[reason]}」{n > 1 ? ` ×${n}` : ""}
              </span>
            ))}
            {gap.last_at && <span className="tabular-nums">{clock(gap.last_at)}</span>}
          </p>
        </div>
        {added ? (
          <span className="shrink-0 pt-0.5 text-sm text-muted">已补进</span>
        ) : (
          <button type="button" onClick={() => setEditing((open) => !open)} aria-expanded={editing} className="btn btn-sm shrink-0">
            {editing ? "收起" : "补进知识库"}
          </button>
        )}
      </div>

      {gap.answer_hint && (
        <details className="mt-1.5 text-sm text-muted">
          <summary className="cursor-pointer select-none">被点踩的那条回答</summary>
          <p className="mt-1 pl-4 break-words whitespace-pre-wrap">{gap.answer_hint}</p>
        </details>
      )}

      {editing && !added && (
        <form
          className="rise-in mt-2.5"
          onSubmit={(event) => {
            event.preventDefault();
            if (text) void save();
          }}
        >
          <label className="field-label" htmlFor={`gap-${gap.question}`}>
            这个问题的标准答案（会作为一篇资料存进本校知识库）
          </label>
          <textarea
            id={`gap-${gap.question}`}
            autoFocus
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            rows={4}
            className="field field-sizing-content mt-1 max-h-60 min-h-24 resize-none"
            placeholder="写清楚条件、时间、去哪办、找谁；有出处的话一并写上"
          />
          <div className="mt-2 flex items-center gap-3">
            <button type="submit" disabled={saving || text.length < 5} className="btn btn-sm btn-primary">
              {saving ? "正在保存…" : "存进知识库"}
            </button>
            <span className="text-[0.8125rem] text-muted">会写成「问：…答：…」，下次类似的问题就能检索到</span>
          </div>
        </form>
      )}
    </li>
  );
}

/** 抽屉页「使用情况」，只给管理员：高频问题、没答上来的、点赞点踩、工具调用。 */
export function UsagePane() {
  const [days, setDays] = useState<UsageDays>(7);
  // 把结果和它对应的天数放在一起：换了天数、新数据还没回来时，自然就是「读取中」
  const [loaded, setLoaded] = useState<{ days: UsageDays; report?: UsageReport; error?: string } | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const report = await fetchUsage(days, controller.signal);
        setLoaded({ days, report });
      } catch (err) {
        if (controller.signal.aborted) return;
        setLoaded({ days, error: errorDetail(err).slice(0, 120) || "读取失败" });
      }
    })();
    return () => controller.abort();
  }, [days]);

  const current = loaded?.days === days ? loaded : null;
  const report = current?.report;
  const peak = Math.max(1, ...(report?.top_questions.map((item) => item.count) ?? []));

  return (
    <section>
      <PaneTitle title="使用情况">看大家问了什么、哪些没答上来，补上资料，助手会越用越准。提问记录是匿名的，倾诉类的不存原文。</PaneTitle>

      <div role="group" aria-label="统计时间范围" className="flex gap-2">
        {PERIODS.map((period) => (
          <button
            key={period.days}
            type="button"
            aria-pressed={days === period.days}
            onClick={() => setDays(period.days)}
            className={`btn btn-sm ${days === period.days ? "btn-primary" : ""}`}
          >
            {period.label}
          </button>
        ))}
      </div>

      {!current && (
        <p className="mt-4 text-muted" role="status">
          正在读取…
        </p>
      )}
      {current?.error && (
        <p className="mt-4 text-[0.9375rem] break-all text-danger" role="alert">
          {current.error}
        </p>
      )}

      {report && (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <Stat
              label="提问"
              value={String(report.totals.questions)}
              note={`其中访客 ${report.totals.guest}，倾诉 ${report.totals.comfort}`}
            />
            <Stat
              label="答上的比例"
              value={percent(report.totals.answer_rate)}
              note={`拒答 ${report.totals.refused}，没找到资料 ${report.totals.no_source}`}
            />
            <Stat label="点赞 / 点踩" value={`${report.totals.up} / ${report.totals.down}`} />
            <Stat
              label="工具失败"
              value={`${report.totals.tool_failures} / ${report.totals.tool_calls}`}
              note="最近的调用，不一定覆盖整个时间范围"
            />
          </div>

          <h3 className="mt-8 font-display text-lg">没答上来的</h3>
          <p className="text-sm text-muted">拒答、没检索到资料、被点踩的问题，说法不同的归在一起。</p>
          {report.unanswered.length === 0 ? (
            <p className="mt-2 text-muted">这段时间没有没答上来的问题。</p>
          ) : (
            <ul className="mt-1">
              {report.unanswered.map((gap) => (
                <GapItem
                  key={gap.question}
                  gap={gap}
                  added={added.has(gap.question)}
                  onAdded={(question) => setAdded((set) => new Set(set).add(question))}
                />
              ))}
            </ul>
          )}

          <h3 className="mt-8 font-display text-lg">大家常问</h3>
          {report.top_questions.length === 0 ? (
            <p className="mt-2 text-muted">这段时间还没有人提问。</p>
          ) : (
            <ol className="mt-2 space-y-2.5">
              {report.top_questions.map((item, index) => (
                <li key={item.question}>
                  <div className="flex items-baseline gap-2">
                    <span className="w-5 shrink-0 text-right text-sm text-muted tabular-nums">{index + 1}</span>
                    <span className="min-w-0 flex-1 break-words">{item.question}</span>
                    <span className="shrink-0 text-sm text-muted tabular-nums">{item.count} 次</span>
                  </div>
                  <div className="mt-1 ml-7" aria-hidden="true">
                    <div className="h-1.5 rounded-full bg-marker" style={{ width: `${Math.max(4, (item.count / peak) * 100)}%` }} />
                  </div>
                </li>
              ))}
            </ol>
          )}

          <h3 className="mt-8 font-display text-lg">工具调用</h3>
          {report.tools.length === 0 ? (
            <p className="mt-2 text-muted">这段时间没有调用过工具。</p>
          ) : (
            <table className="mt-2 w-full table-fixed text-[0.9375rem]">
              <thead>
                <tr className="border-b-2 border-ink text-left text-sm text-muted">
                  <th className="py-1.5 font-normal">工具</th>
                  <th className="w-14 py-1.5 text-right font-normal">次数</th>
                  <th className="w-16 py-1.5 text-right font-normal">失败率</th>
                  <th className="w-20 py-1.5 text-right font-normal">平均耗时</th>
                </tr>
              </thead>
              <tbody>
                {report.tools.map((tool) => (
                  <tr key={tool.name} className="border-b border-dashed border-rule align-top">
                    <td className="py-2 pr-2">
                      <p className="truncate" title={tool.name}>{tool.label}</p>
                      {tool.last_error && tool.failures > 0 && (
                        <p className="truncate text-[0.8125rem] text-muted" title={tool.last_error}>
                          最近一次失败：{tool.last_error}
                        </p>
                      )}
                    </td>
                    <td className="py-2 text-right tabular-nums">{tool.calls}</td>
                    <td className={`py-2 text-right tabular-nums ${tool.failure_rate >= FAILURE_ALERT ? "text-danger" : ""}`}>
                      {percent(tool.failure_rate)}
                    </td>
                    <td className="py-2 text-right text-sm tabular-nums">{seconds(tool.avg_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}
