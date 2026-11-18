"use client";

import { useState } from "react";
import { DecompressionCabin } from "@/components/DecompressionCabin";
import { MemoryPane } from "@/components/MemoryPane";
import { StudySpacePane } from "@/components/StudySpacePane";
import { useToast } from "@/components/Toast";
import { PET_COMPANION_EVENT } from "@/hooks/useDeskPet";
import {
  type CabinState,
  type CompanionTodo,
  type MoodJournal,
  type WellnessDashboard,
  createWellnessJournal,
  createWellnessTodo,
  deleteWellnessJournal,
  deleteWellnessTodo,
  errorDetail,
  toggleWellnessTodo,
} from "@/lib/api";

const MOOD_PRESETS = ["平静", "开心", "疲惫", "焦虑", "委屈", "迷茫"] as const;

function MoodSparkline({ journals }: { journals: MoodJournal[] }) {
  // 按时间正序绘出近 14 条能量走势
  const points = [...journals].reverse().slice(-14);
  if (points.length === 0) {
    return (
      <p className="rounded border border-dashed border-rule bg-skin/40 p-3 text-xs text-muted">
        还没有情绪手账记录。在对话里吐槽倾诉，或点击下方「记一下此刻心情」，就能逐渐画出你的能量起伏曲线。
      </p>
    );
  }
  const w = 320;
  const h = 86;
  const padX = 14;
  const padY = 12;
  const coords = points.map((item, idx) => {
    const x = points.length === 1 ? w / 2 : padX + (idx / (points.length - 1)) * (w - padX * 2);
    const y = h - padY - ((item.energy - 1) / 9) * (h - padY * 2);
    return { x, y, item };
  });
  const pathD = coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)} ${c.y.toFixed(1)}`).join(" ");

  return (
    <div className="rounded border border-dashed border-rule bg-skin/30 p-3">
      <svg viewBox={`0 0 ${w} ${h}`} className="h-24 w-full overflow-visible" aria-label="近期情绪能量曲线">
        <line x1={padX} y1={h / 2} x2={w - padX} y2={h / 2} stroke="currentColor" strokeOpacity="0.14" strokeDasharray="3 3" />
        <path d={pathD} fill="none" stroke="var(--color-pink)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        {coords.map((c) => (
          <g key={c.item.id}>
            <circle cx={c.x} cy={c.y} r="4.2" fill="var(--color-paper)" stroke="var(--color-ink)" strokeWidth="2" />
            <title>{`${c.item.mood} · 能量 ${c.item.energy}/10 · ${c.item.summary}`}</title>
          </g>
        ))}
      </svg>
      <div className="mt-1 flex justify-between text-[0.7rem] text-muted">
        <span>较早 ({points[0]?.mood} {points[0]?.energy}/10)</span>
        <span>最新 ({points[points.length - 1]?.mood} {points[points.length - 1]?.energy}/10)</span>
      </div>
    </div>
  );
}

interface Props {
  dashboard: WellnessDashboard | null;
  loading: boolean;
  jwOn: boolean;
  onRefresh: () => Promise<void>;
  onUpdateCabin: (patch: Partial<CabinState>) => void;
  onAskInChat: (question: string) => void;
}

type ArchiveSubTab = "wellness" | "memory" | "study";

export function ArchivePane({
  dashboard,
  loading,
  jwOn,
  onRefresh,
  onUpdateCabin,
  onAskInChat,
}: Props) {
  const toast = useToast();
  const [subTab, setSubTab] = useState<ArchiveSubTab>("wellness");

  // 微待办快速新增
  const [todoDraft, setTodoDraft] = useState("");
  const [todoBusy, setTodoBusy] = useState(false);

  // 快速情绪手账打卡
  const [journalOpen, setJournalOpen] = useState(false);
  const [mood, setMood] = useState("平静");
  const [energy, setEnergy] = useState(6);
  const [stressor, setStressor] = useState("学业");
  const [summary, setSummary] = useState("");
  const [followupNote, setFollowupNote] = useState("");
  const [journalBusy, setJournalBusy] = useState(false);

  async function handleAddTodo() {
    const title = todoDraft.trim();
    if (!title) return;
    setTodoBusy(true);
    try {
      await createWellnessTodo({ title, est_minutes: 20 });
      setTodoDraft("");
      await onRefresh();
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setTodoBusy(false);
    }
  }

  async function handleToggleTodo(item: CompanionTodo) {
    try {
      const updated = await toggleWellnessTodo(item.id, item.status !== "done");
      if (updated.status === "done") {
        window.dispatchEvent(
          new CustomEvent(PET_COMPANION_EVENT, {
            detail: { mood: "cheering", text: `完成「${updated.title.slice(0, 12)}」啦，真棒！` },
          }),
        );
      }
      await onRefresh();
    } catch (err) {
      toast.error(errorDetail(err));
    }
  }

  async function handleDeleteTodo(todoId: string) {
    try {
      await deleteWellnessTodo(todoId);
      await onRefresh();
    } catch (err) {
      toast.error(errorDetail(err));
    }
  }

  async function handleAddJournal() {
    const text = summary.trim();
    if (!text) return;
    setJournalBusy(true);
    try {
      await createWellnessJournal({
        mood,
        energy,
        stressor: stressor.trim() || "日常",
        summary: text,
        followup_hours: followupNote.trim() ? 18 : undefined,
        followup_note: followupNote.trim(),
      });
      setSummary("");
      setFollowupNote("");
      setJournalOpen(false);
      toast.success("已记入树洞情绪手账");
      await onRefresh();
    } catch (err) {
      toast.error(errorDetail(err));
    } finally {
      setJournalBusy(false);
    }
  }

  async function handleDeleteJournal(id: string) {
    try {
      await deleteWellnessJournal(id);
      await onRefresh();
    } catch (err) {
      toast.error(errorDetail(err));
    }
  }

  const todos = dashboard?.todos ?? [];
  const pendingTodos = todos.filter((t) => t.status === "pending");
  const doneTodos = todos.filter((t) => t.status === "done");
  const journals = dashboard?.journals ?? [];
  const cabin = dashboard?.cabin ?? {
    ambient_sound: "none",
    breathing_mode: "none",
    focus_minutes: 25,
    night_mode: false,
    pet_mood: "normal",
    note: "",
    updated_at: "",
  };
  const mcp = dashboard?.mcp;

  return (
    <div className="space-y-5 pb-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-2xl leading-tight">情绪与记忆档案</h1>
          <p className="text-sm text-muted">你的心情走势、MCP 拆解的微待办、白噪音减压舱与伴侣长期记忆</p>
        </div>
        <div className="flex gap-1.5">
          {([
            ["wellness", "减压与心情"],
            ["memory", "长期记忆"],
            ["study", "课程伴学"],
          ] as [ArchiveSubTab, string][]).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSubTab(id)}
              className={`btn btn-sm ${subTab === id ? "btn-primary" : ""}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {subTab === "memory" && <MemoryPane />}
      {subTab === "study" && <StudySpacePane jwOn={jwOn} onAsk={onAskInChat} />}

      {subTab === "wellness" && (
        <div className="space-y-5">
          {loading && !dashboard && <p className="text-sm text-muted">正在读取你的减压档案…</p>}

          {/* 1. 减压伴学舱 */}
          <DecompressionCabin cabin={cabin} onUpdate={onUpdateCabin} />

          {/* 2. 压力拆解微待办 */}
          <section className="sketch bg-paper p-4 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="font-display text-lg">✅ 压力拆解微待办 ({pendingTodos.length} 待完成)</h3>
                <p className="text-xs text-muted">
                  在对话中吐槽 DDL 堆积，伴侣会自动通过 MCP 帮你切分成 15~30 分钟可立即动手的小任务
                </p>
              </div>
              <button
                type="button"
                onClick={() => onAskInChat("帮我看看目前待办，结合我这几天的课表安排一下今晚先做哪一个 20 分钟小任务？")}
                className="btn btn-sm"
              >
                让伴侣帮排优先级
              </button>
            </div>

            <form
              className="flex items-end gap-2.5"
              onSubmit={(e) => {
                e.preventDefault();
                void handleAddTodo();
              }}
            >
              <input
                value={todoDraft}
                onChange={(e) => setTodoDraft(e.target.value)}
                placeholder="手动记一个 20 分钟小步，例如：整理高数第3章公式"
                className="field flex-1 text-sm"
              />
              <button type="submit" disabled={todoBusy || !todoDraft.trim()} className="btn btn-sm btn-primary">
                添加
              </button>
            </form>

            {todos.length === 0 ? (
              <p className="text-sm text-muted py-1">还没有微待办。先和伴侣说说最近让你发愁的事吧～</p>
            ) : (
              <ul className="divide-y divide-dashed divide-rule">
                {[...pendingTodos, ...doneTodos.slice(0, 6)].map((item) => (
                  <li key={item.id} className="flex items-start justify-between gap-3 py-2.5">
                    <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2.5">
                      <input
                        type="checkbox"
                        checked={item.status === "done"}
                        onChange={() => void handleToggleTodo(item)}
                        className="mt-1 size-4 shrink-0 accent-pink"
                      />
                      <span className="min-w-0 flex-1">
                        <span className={`block text-sm font-medium ${item.status === "done" ? "line-through text-muted" : ""}`}>
                          {item.title}
                        </span>
                        <span className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                          <span>约 {item.est_minutes} 分钟</span>
                          {item.due_label && <span>· {item.due_label}</span>}
                          {item.source === "mcp_decompose" && (
                            <span className="rounded bg-flower/25 px-1.5 py-0.2 text-ink">MCP 拆解</span>
                          )}
                          {item.detail && <span>· {item.detail}</span>}
                        </span>
                      </span>
                    </label>
                    <button
                      type="button"
                      onClick={() => void handleDeleteTodo(item.id)}
                      className="link-ink shrink-0 text-xs text-muted"
                    >
                      删掉
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* 3. 情绪轨迹与树洞手账 */}
          <section className="sketch bg-paper p-4 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="font-display text-lg">
                  🌤️ 近期情绪能量轨迹
                  {dashboard?.avg_energy != null && (
                    <span className="ml-2 text-sm font-normal text-muted">
                      14日均值 {dashboard.avg_energy}/10
                    </span>
                  )}
                </h3>
                <p className="text-xs text-muted">隐私已自动脱敏，仅用于让伴侣记住你连续几天的情绪高低与关心回访</p>
              </div>
              <button type="button" onClick={() => setJournalOpen((v) => !v)} className="btn btn-sm">
                {journalOpen ? "收起打卡" : "+ 记一下此刻心情"}
              </button>
            </div>

            {journalOpen && (
              <div className="rounded border border-dashed border-rule bg-skin/40 p-3 space-y-3">
                <div className="flex flex-wrap gap-1.5">
                  {MOOD_PRESETS.map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setMood(m)}
                      className={`rounded-full px-2.5 py-0.5 text-xs border border-rule cursor-pointer ${
                        mood === m ? "bg-pink text-ink font-medium" : "bg-paper text-muted"
                      }`}
                    >
                      {m}
                    </button>
                  ))}
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block">
                    <span className="field-label">当前能量值 ({energy}/10)</span>
                    <input
                      type="range"
                      min={1}
                      max={10}
                      value={energy}
                      onChange={(e) => setEnergy(Number(e.target.value))}
                      className="mt-1.5 w-full accent-pink"
                    />
                  </label>
                  <label className="block">
                    <span className="field-label">主要牵挂 / 压力源</span>
                    <input
                      value={stressor}
                      onChange={(e) => setStressor(e.target.value)}
                      placeholder="例如：学业 / 考试 / 人际 / 作息"
                      className="field text-sm"
                    />
                  </label>
                </div>
                <textarea
                  value={summary}
                  onChange={(e) => setSummary(e.target.value)}
                  rows={2}
                  placeholder="简单写一两句此刻的感受（树洞仅自己可见）"
                  className="field text-sm"
                />
                <input
                  value={followupNote}
                  onChange={(e) => setFollowupNote(e.target.value)}
                  placeholder="可选：希望明天企微怎么关心你？（例如：问问上午小组答辩顺不顺利）"
                  className="field text-sm"
                />
                <button
                  type="button"
                  disabled={journalBusy || !summary.trim()}
                  onClick={() => void handleAddJournal()}
                  className="btn btn-sm btn-primary"
                >
                  {journalBusy ? "记录中…" : "存进情绪手账"}
                </button>
              </div>
            )}

            <MoodSparkline journals={journals} />

            {journals.length > 0 && (
              <ul className="divide-y divide-dashed divide-rule">
                {journals.slice(0, 8).map((j) => (
                  <li key={j.id} className="flex items-start justify-between gap-3 py-2 text-sm">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded-full bg-skin px-2 py-0.5 text-xs font-medium">{j.mood}</span>
                        <span className="text-xs text-muted tabular-nums">能量 {j.energy}/10</span>
                        <span className="text-xs text-muted">· {j.stressor}</span>
                        {j.followup_at && (
                          <span className="text-xs text-pink">
                            {j.followup_sent ? "已回访" : "已预约次日企微关心"}
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-sm">{j.summary}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => void handleDeleteJournal(j.id)}
                      className="link-ink shrink-0 text-xs text-muted"
                    >
                      删除
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* 4. MCP 协议通道状态（方便答辩直观展示协议发现与工具清单） */}
          {mcp && (
            <section className="sketch bg-paper p-4 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h3 className="font-display text-base">🔌 MCP 工具协议实时状态 (stdio JSON-RPC)</h3>
                <span className="rounded-full bg-flower/25 px-2.5 py-0.5 text-xs">
                  {mcp.servers.filter((s) => s.connected).length} 个服务已挂载 · {mcp.tools.length} 个工具
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5 pt-1">
                {mcp.tools.map((tool) => (
                  <span
                    key={tool.name}
                    title={tool.description}
                    className="rounded border border-rule bg-skin/50 px-2 py-0.5 text-xs"
                  >
                    {tool.title} <code className="text-[0.7rem] text-muted">({tool.name})</code>
                  </span>
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
