"use client";

import { useCallback, useEffect, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import { useToast } from "@/components/Toast";
import {
  type HelpTicket,
  type TicketCategory,
  type TicketStatus,
  createTicket,
  errorDetail,
  fetchAdminTickets,
  fetchMyTickets,
  mergeTicketToKb,
  replyTicket,
} from "@/lib/api";

export const TICKET_CATEGORIES: { value: TicketCategory; label: string }[] = [
  { value: "academic", label: "教务学业" },
  { value: "campus", label: "生活资助" },
  { value: "tech", label: "账号系统" },
  { value: "other", label: "其他" },
];

const STATUS_BADGE: Record<TicketStatus, { label: string; tone: string }> = {
  open: { label: "待处理", tone: "bg-pollen text-ink" },
  answered: { label: "已回复", tone: "bg-skin text-ink" },
  merged: { label: "已入知识库", tone: "bg-flower/25 text-ink" },
  closed: { label: "已关闭", tone: "bg-desk text-muted" },
};

function formatTime(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso.slice(0, 16);
  }
}

function AdminTicketCard({
  ticket,
  onUpdated,
}: {
  ticket: HelpTicket;
  onUpdated: (next: HelpTicket) => void;
}) {
  const toast = useToast();
  const [reply, setReply] = useState(ticket.reply || "");
  const [kbTitle, setKbTitle] = useState("");
  const [busy, setBusy] = useState<"reply" | "merge" | null>(null);
  const badge = STATUS_BADGE[ticket.status] ?? STATUS_BADGE.open;

  async function handleReply() {
    if (!reply.trim()) return;
    setBusy("reply");
    try {
      const updated = await replyTicket(ticket.id, reply.trim());
      onUpdated(updated);
      toast.success("工单回复已发送给同学");
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleMerge() {
    if (!reply.trim()) {
      toast.error("请先填写解答内容再回流进知识库");
      return;
    }
    setBusy("merge");
    try {
      const res = await mergeTicketToKb(ticket.id, { reply: reply.trim(), title: kbTitle.trim() });
      onUpdated(res.ticket);
      toast.success(`已回复并回流进学校知识库（${res.document}）`);
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className="sketch bg-paper p-3.5 space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-2 py-0.5 text-xs ${badge.tone}`}>{badge.label}</span>
          <span className="rounded border border-dashed border-rule px-1.5 py-0.5">{ticket.category_label}</span>
          <span>
            {ticket.requester_name || "同学"}
            {ticket.requester_student_no ? ` (${ticket.requester_student_no})` : ""}
          </span>
        </div>
        <span className="tabular-nums">{formatTime(ticket.created_at)}</span>
      </div>

      <p className="font-medium leading-snug text-ink">{ticket.question}</p>
      {ticket.context_summary && (
        <p className="border-l-2 border-dashed border-rule pl-2.5 text-sm text-muted whitespace-pre-wrap">
          {ticket.context_summary}
        </p>
      )}

      <div className="space-y-2 border-t border-dashed border-rule pt-2.5">
        <label className="block">
          <span className="field-label">人工核准答复</span>
          <textarea
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            rows={3}
            placeholder="写明具体处理办法、联系人/窗口地点、材料清单与依据"
            className="field field-sizing-content min-h-20 resize-none"
          />
        </label>
        <input
          value={kbTitle}
          onChange={(e) => setKbTitle(e.target.value)}
          placeholder="入库标题（可选，默认使用原提问）"
          className="field text-sm"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy !== null || !reply.trim()}
            onClick={() => void handleReply()}
            className="btn btn-sm"
          >
            {busy === "reply" ? "保存中…" : "仅回复学生"}
          </button>
          <button
            type="button"
            disabled={busy !== null || !reply.trim()}
            onClick={() => void handleMerge()}
            className="btn btn-sm btn-primary"
          >
            {busy === "merge"
              ? "回流中…"
              : ticket.status === "merged"
                ? "更新答复并重写入库"
                : "回复并一键回流知识库"}
          </button>
        </div>
      </div>
    </li>
  );
}

/** 抽屉页「求助工单」：学生发工单与看回复；教工/管理员处理工单并一键回流知识库。 */
export function TicketsPane({ isStaff }: { isStaff: boolean }) {
  const toast = useToast();
  const [mode, setMode] = useState<"queue" | "mine">(isStaff ? "queue" : "mine");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [myTickets, setMyTickets] = useState<HelpTicket[]>([]);
  const [adminTickets, setAdminTickets] = useState<HelpTicket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [creating, setCreating] = useState(false);
  const [category, setCategory] = useState<TicketCategory>("academic");
  const [question, setQuestion] = useState("");
  const [contextSummary, setContextSummary] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (mode === "queue" && isStaff) {
        setAdminTickets(await fetchAdminTickets(statusFilter));
      } else {
        setMyTickets(await fetchMyTickets());
      }
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setLoading(false);
    }
  }, [isStaff, mode, statusFilter]);

  useEffect(() => {
    let cancelled = false;
    const req = mode === "queue" && isStaff ? fetchAdminTickets(statusFilter) : fetchMyTickets();
    req
      .then((rows) => {
        if (cancelled) return;
        setError(null);
        if (mode === "queue" && isStaff) {
          setAdminTickets(rows);
        } else {
          setMyTickets(rows);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(errorDetail(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isStaff, mode, statusFilter]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setSubmitting(true);
    try {
      const created = await createTicket({
        question: question.trim(),
        category,
        context_summary: contextSummary.trim(),
      });
      setMyTickets((prev) => [created, ...prev]);
      setQuestion("");
      setContextSummary("");
      setCreating(false);
      toast.success("工单已提交，老师回复后会同步推送通知");
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="space-y-4">
      <PaneTitle title="人工求助工单">
        {isStaff
          ? "查看学生转人工的问题并回复，确认后的标准解答可一键回流进学校知识库。"
          : "AI 没查到或拿不准的事可以直接转给老师/信息员解答，回复后可随时在这里查看。"}
      </PaneTitle>

      {isStaff && (
        <div role="tablist" className="flex gap-2">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "queue"}
            onClick={() => {
              setLoading(true);
              setMode("queue");
            }}
            className={`btn btn-sm ${mode === "queue" ? "btn-primary" : ""}`}
          >
            待处理工单队列
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "mine"}
            onClick={() => {
              setLoading(true);
              setMode("mine");
            }}
            className={`btn btn-sm ${mode === "mine" ? "btn-primary" : ""}`}
          >
            我发起的求助
          </button>
        </div>
      )}

      {mode === "queue" && isStaff ? (
        <>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="工单状态筛选">
            {[
              { value: "", label: "全部" },
              { value: "open", label: "待处理" },
              { value: "answered", label: "已回复" },
              { value: "merged", label: "已入知识库" },
            ].map((item) => (
              <button
                key={item.value || "all"}
                type="button"
                aria-pressed={statusFilter === item.value}
                onClick={() => {
                  setLoading(true);
                  setStatusFilter(item.value);
                }}
                className={`btn btn-sm ${statusFilter === item.value ? "btn-primary" : ""}`}
              >
                {item.label}
              </button>
            ))}
          </div>

          {loading ? (
            <p className="text-muted">正在加载工单…</p>
          ) : error ? (
            <p className="text-sm text-danger" role="alert">
              {error}
            </p>
          ) : adminTickets.length === 0 ? (
            <p className="sketch bg-skin px-3 py-2 text-sm text-muted">当前筛选下没有求助工单。</p>
          ) : (
            <ul className="space-y-3">
              {adminTickets.map((t) => (
                <AdminTicketCard
                  key={t.id}
                  ticket={t}
                  onUpdated={(next) =>
                    setAdminTickets((prev) => prev.map((row) => (row.id === next.id ? next : row)))
                  }
                />
              ))}
            </ul>
          )}
        </>
      ) : (
        <>
          <div className="flex items-center justify-between gap-2">
            <button type="button" onClick={() => setCreating((v) => !v)} className="btn btn-sm btn-primary">
              {creating ? "收起表单" : "+ 发起新求助"}
            </button>
            <button type="button" onClick={() => void load()} className="btn btn-sm">
              刷新状态
            </button>
          </div>

          {creating && (
            <form onSubmit={handleCreate} className="sketch bg-paper p-3.5 space-y-2.5">
              <div className="flex flex-wrap gap-1.5">
                {TICKET_CATEGORIES.map((c) => (
                  <button
                    key={c.value}
                    type="button"
                    aria-pressed={category === c.value}
                    onClick={() => setCategory(c.value)}
                    className={`btn btn-sm ${category === c.value ? "btn-primary" : ""}`}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
              <label className="block">
                <span className="field-label">遇到了什么问题</span>
                <textarea
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  rows={2}
                  required
                  placeholder="例如：转专业补修学分认定需要交哪些材料？"
                  className="field"
                />
              </label>
              <label className="block">
                <span className="field-label">补充说明 / 专业年级（可选）</span>
                <textarea
                  value={contextSummary}
                  onChange={(e) => setContextSummary(e.target.value)}
                  rows={2}
                  placeholder="可附上当前办理环节或截图识别到的错误提示"
                  className="field text-sm"
                />
              </label>
              <button type="submit" disabled={submitting || !question.trim()} className="btn btn-sm btn-primary">
                {submitting ? "提交中…" : "提交人工工单"}
              </button>
            </form>
          )}

          {loading ? (
            <p className="text-muted">正在读取…</p>
          ) : error ? (
            <p className="text-sm text-danger" role="alert">
              {error}
            </p>
          ) : myTickets.length === 0 ? (
            <p className="sketch bg-skin px-3 py-2 text-sm text-muted">
              还没有提交过求助工单。在任意 AI 回答下方点救生圈按钮「转人工求助」即可一键提交。
            </p>
          ) : (
            <ul className="space-y-3">
              {myTickets.map((t) => {
                const badge = STATUS_BADGE[t.status] ?? STATUS_BADGE.open;
                return (
                  <li key={t.id} className="sketch bg-paper p-3.5 space-y-2">
                    <div className="flex items-center justify-between gap-2 text-xs text-muted">
                      <div className="flex items-center gap-2">
                        <span className={`rounded-full px-2 py-0.5 ${badge.tone}`}>{badge.label}</span>
                        <span>{t.category_label}</span>
                      </div>
                      <span className="tabular-nums">{formatTime(t.created_at)}</span>
                    </div>
                    <p className="font-medium leading-snug">{t.question}</p>
                    {t.reply ? (
                      <div className="rounded border border-dashed border-rule bg-skin/60 p-2.5 text-sm space-y-1">
                        <p className="text-xs text-muted">
                          {t.replied_by_name ? `${t.replied_by_name} 回复` : "人工回复"}
                          {t.replied_at ? ` · ${formatTime(t.replied_at)}` : ""}
                        </p>
                        <p className="whitespace-pre-wrap leading-relaxed">{t.reply}</p>
                      </div>
                    ) : (
                      <p className="text-sm text-muted">已进入学校处理队列，请耐心等待老师或学长回复～</p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
