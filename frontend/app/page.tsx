"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArchivePane } from "@/components/ArchivePane";
import { ChatHeader, type CoreView } from "@/components/ChatHeader";
import { ChatLog } from "@/components/ChatLog";
import { Composer, type ComposerPrefill } from "@/components/Composer";
import { ConnectPane } from "@/components/ConnectPane";
import { DecompressionCabin } from "@/components/DecompressionCabin";
import { DeskPet } from "@/components/DeskPet";
import { SelectionAsk } from "@/components/SelectionAsk";
import { SessionList } from "@/components/SessionList";
import { ArrowDownIcon } from "@/components/SketchIcons";
import { SuggestedQuestions } from "@/components/SuggestedQuestions";
import { TodayCard } from "@/components/TodayCard";
import { useToast } from "@/components/Toast";
import { PET_COMPANION_EVENT, PET_LOOK_EVENT } from "@/hooks/useDeskPet";
import { useLessonReminders } from "@/hooks/useLessonReminders";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { useToday } from "@/hooks/useToday";
import { useChat } from "@/hooks/useChat";
import {
  type CabinState,
  type CompanionEffect,
  type TicketCategory,
  type WellnessDashboard,
  createTicket,
  errorDetail,
  fetchWellnessDashboard,
  fetchWorkspace,
  patchWellnessCabin,
  toggleWellnessTodo,
} from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { canManageKnowledge } from "@/lib/auth/session";
import { isFallbackAnswer } from "@/lib/friendly-error";
import { useReminderSettings } from "@/lib/reminder-settings";
import { speakText, useSpeechSettings } from "@/lib/speech-settings";

const DEFAULT_ASSISTANT_NAME = "校园伴侣";

function quoteBlock(text: string): string {
  return `${text
    .split("\n")
    .map((line) => `> ${line}`)
    .join("\n")}\n\n`;
}

export default function ChatPage() {
  const { user, logout } = useAuth();
  const isStaff = canManageKnowledge(user?.role);
  const isAdmin = user?.role === "ADMIN";
  const [speech] = useSpeechSettings();
  const { notice, success, error: toastError } = useToast();
  const { state: today, reload: reloadToday } = useToday();
  const [reminder, setReminder] = useReminderSettings();
  useLessonReminders(today.status === "ready" ? today.card : null, reminder, notice);

  const [view, setView] = useState<CoreView>("chat");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [assistantName, setAssistantName] = useState<string | null>(null);
  const [visionOn, setVisionOn] = useState(false);
  const [prefill, setPrefill] = useState<ComposerPrefill>({ text: "", n: 0 });
  const [wellness, setWellness] = useState<WellnessDashboard | null>(null);
  const [wellnessLoading, setWellnessLoading] = useState(false);
  const [nightOverride, setNightOverride] = useState<boolean | null>(null);

  const loadWorkspace = useCallback(() => {
    fetchWorkspace()
      .then((workspace) => {
        setAssistantName(workspace.assistant_name || DEFAULT_ASSISTANT_NAME);
        setVisionOn(workspace.vision === true);
      })
      .catch(() => setAssistantName((n) => n ?? DEFAULT_ASSISTANT_NAME));
  }, []);
  useEffect(loadWorkspace, [loadWorkspace]);

  const reloadWellness = useCallback(async () => {
    setWellnessLoading(true);
    try {
      const data = await fetchWellnessDashboard(14);
      setWellness(data);
    } catch {
      // 访客或离线时静默降级
    } finally {
      setWellnessLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    fetchWellnessDashboard(14)
      .then((data) => {
        if (!cancelled) setWellness(data);
      })
      .catch(() => {
        // 访客或离线时静默降级
      });
    return () => {
      cancelled = true;
    };
  }, [user]);

  // 同步企微免登链接带入的 ?view=archive / connect 参数
  useEffect(() => {
    const param = new URLSearchParams(window.location.search).get("view");
    if (param === "archive" || param === "connect" || param === "chat") {
      queueMicrotask(() => setView(param));
    }
  }, []);

  const nightMode = nightOverride ?? wellness?.cabin.night_mode ?? false;

  useEffect(() => {
    document.documentElement.setAttribute("data-night", nightMode ? "true" : "false");
    return () => {
      document.documentElement.removeAttribute("data-night");
    };
  }, [nightMode]);

  const handleCompanionEffect = useCallback(
    (effect: CompanionEffect) => {
      if (effect.pet_mood) {
        let bubbleText = effect.note;
        if (!bubbleText && effect.kind === "todos_added" && effect.count) {
          bubbleText = `帮你在减压清单拆好了 ${effect.count} 个小步～`;
        }
        window.dispatchEvent(
          new CustomEvent(PET_COMPANION_EVENT, {
            detail: { mood: effect.pet_mood, text: bubbleText },
          }),
        );
      }
      if (typeof effect.night_mode === "boolean") {
        setNightOverride(effect.night_mode);
      }
      if (effect.kind === "cabin_updated") {
        setWellness((prev) =>
          prev
            ? {
                ...prev,
                cabin: {
                  ...prev.cabin,
                  ambient_sound: effect.ambient_sound ?? prev.cabin.ambient_sound,
                  breathing_mode: effect.breathing_mode ?? prev.cabin.breathing_mode,
                  focus_minutes: effect.focus_minutes ?? prev.cabin.focus_minutes,
                  night_mode: effect.night_mode ?? prev.cabin.night_mode,
                  pet_mood: effect.pet_mood ?? prev.cabin.pet_mood,
                  note: effect.note ?? prev.cabin.note,
                },
              }
            : prev,
        );
      }
      if (effect.kind && effect.kind !== "listening") {
        void reloadWellness();
      }
    },
    [reloadWellness],
  );

  const handleUpdateCabin = useCallback(
    async (patch: Partial<CabinState>) => {
      if (typeof patch.night_mode === "boolean") {
        setNightOverride(patch.night_mode);
      }
      setWellness((prev) => (prev ? { ...prev, cabin: { ...prev.cabin, ...patch } } : prev));
      try {
        const next = await patchWellnessCabin(patch);
        setWellness((prev) => (prev ? { ...prev, cabin: next } : prev));
      } catch (err) {
        toastError(errorDetail(err));
      }
    },
    [toastError],
  );

  const name = assistantName ?? "";

  const speak = useCallback(
    (text: string) => {
      if (speech.enabled && !isFallbackAnswer(text)) speakText(text, speech);
    },
    [speech],
  );

  const chat = useChat(speak, { onCompanion: handleCompanionEffect });
  const { messages, pending, progress, error, restoring, busy, threadId } = chat;
  const last = messages.at(-1);
  const answerFailed = last?.role === "assistant" && isFallbackAnswer(last.content);
  const lastQuestion = messages.findLast((message) => message.role === "user")?.content;

  const scrollRef = useRef<HTMLElement>(null);
  const { atBottom, unseen, scrollToBottom } = useStickToBottom(
    scrollRef,
    `${messages.length}:${pending.length}:${progress?.phase}`,
  );

  const closeHistory = useCallback(() => setHistoryOpen(false), []);

  function handleSend(text: string, images?: string[]) {
    void chat.send(text, { images });
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const asked = document.querySelectorAll("[data-role=user]");
        const rect = asked[asked.length - 1]?.getBoundingClientRect();
        if (rect) {
          window.dispatchEvent(
            new CustomEvent(PET_LOOK_EVENT, { detail: { x: rect.right - 40, y: rect.top + rect.height / 2 } }),
          );
        }
      }),
    );
  }

  function handleNewSession() {
    window.speechSynthesis?.cancel();
    setHistoryOpen(false);
    setView("chat");
    void chat.startNewSession();
  }

  function handleOpenHistory() {
    setHistoryOpen(true);
    void chat.refreshSessions();
  }

  function handleOpenSession(tid: string) {
    window.speechSynthesis?.cancel();
    setHistoryOpen(false);
    setView("chat");
    void chat.openSession(tid);
  }

  function handleLogout() {
    window.speechSynthesis?.cancel();
    logout();
  }

  const jwOn = today.status === "ready" && today.card.enabled;
  async function handleEscalate(info: {
    question: string;
    messageId: string;
    answer: string;
    category: TicketCategory;
    note: string;
  }) {
    const snippet = info.answer.trim().slice(0, 240);
    const context_summary = info.note
      ? `学生补充：${info.note}\n\n原 AI 回答摘要：${snippet}`
      : `原 AI 回答摘要：${snippet}`;
    await createTicket({
      question: info.question,
      category: info.category,
      message_id: info.messageId,
      context_summary,
    });
    success("已转达给校方/学长老师跟进，回复后会第一时间通过企微推送给你");
  }

  const pendingTodos = (wellness?.todos ?? []).filter((t) => t.status === "pending");
  const cabin = wellness?.cabin ?? {
    ambient_sound: "none" as const,
    breathing_mode: "none" as const,
    focus_minutes: 25,
    night_mode: nightMode,
    pet_mood: "normal" as const,
    note: "",
    updated_at: "",
  };
  const showCompactCabin = cabin.ambient_sound !== "none" || cabin.breathing_mode !== "none";
  const noteProps = { canDebug: isStaff, canConfigure: isAdmin, onOpenService: () => setView("connect") };

  return (
    <div className="flex h-dvh flex-col lg:px-4 lg:py-4">
      <div className="mx-auto flex h-full w-full max-w-[74rem] gap-4">
        {/* 主容器：手机竖屏全屏铺满，桌面端主纸页居中/偏左配合右侧驾驶舱 */}
        <div className="sketch-lg relative flex h-full min-w-0 flex-1 flex-col bg-paper">
          <ChatHeader
            assistantName={name}
            user={user}
            isStaff={isStaff}
            threadId={threadId}
            activeView={view}
            pendingTodoCount={pendingTodos.length}
            nightMode={nightMode}
            onChangeView={setView}
            onToggleNightMode={() => void handleUpdateCabin({ night_mode: !nightMode })}
            onNewSession={handleNewSession}
            onOpenHistory={handleOpenHistory}
            onLogout={handleLogout}
          />

          {view === "chat" && (
            <>
              <main ref={scrollRef} className="relative flex-1 overflow-y-auto">
                <div className="mx-auto flex max-w-[45rem] flex-col gap-6 px-4 pt-6 pb-28 sm:px-8 md:pb-10">
                  {showCompactCabin && (
                    <div className="lg:hidden">
                      <DecompressionCabin cabin={cabin} compact onUpdate={(p) => void handleUpdateCabin(p)} />
                    </div>
                  )}

                  {restoring ? (
                    <p className="text-center text-muted">正在翻到上次那一页…</p>
                  ) : (
                    <>
                      {messages.length === 0 && (
                        <div className="rise-in space-y-5">
                          <div>
                            <h1 className="font-display text-[1.85rem] leading-tight sm:text-[2.35rem]">
                              嗨，我是{name}
                            </h1>
                            <p className="mt-1.5 text-sm text-muted sm:text-base">
                              懂你课表与情绪的校园伴侣：心烦失眠时聊两句、顺手通过 MCP 帮你拆微待办或开雨声减压；查课查校规也没问题。
                            </p>
                          </div>
                          <TodayCard
                            state={today}
                            reminder={reminder}
                            onReminderChange={setReminder}
                            onRetry={reloadToday}
                            onBind={() => setView("connect")}
                            onCalendar={() => setView("connect")}
                          />
                          <SuggestedQuestions disabled={busy || !threadId} onAsk={handleSend} />
                        </div>
                      )}
                      <ChatLog
                        messages={messages}
                        pending={pending}
                        progress={progress}
                        error={error}
                        busy={busy}
                        assistantName={name}
                        showDebug={isStaff}
                        noteProps={noteProps}
                        onRegenerate={chat.regenerate}
                        onEdit={chat.editLast}
                        onRate={chat.rate}
                        onEscalate={handleEscalate}
                        onAsk={handleSend}
                      />
                    </>
                  )}
                </div>
              </main>

              {!atBottom && (
                <button
                  type="button"
                  onClick={scrollToBottom}
                  aria-label="回到最新"
                  className="btn btn-icon pop-in absolute right-4 bottom-32 z-10 rounded-full shadow-[2px_3px_0_rgba(27,18,20,0.12)] sm:right-8 md:bottom-24"
                >
                  <ArrowDownIcon size={18} />
                  {unseen && (
                    <span
                      className="absolute -top-0.5 -right-0.5 size-2.5 rounded-full border border-ink bg-pink"
                      aria-hidden="true"
                    />
                  )}
                </button>
              )}

              <div className="border-t-2 border-ink px-3 py-2.5 sm:px-6 md:py-3">
                <div className="mx-auto max-w-[45rem]">
                  <Composer
                    disabled={restoring || !threadId}
                    busy={busy}
                    onSend={handleSend}
                    onStop={chat.stop}
                    prefill={prefill}
                    lastQuestion={lastQuestion}
                    allowImages={visionOn}
                  />
                </div>
              </div>
            </>
          )}

          {view === "archive" && (
            <main className="flex-1 overflow-y-auto px-4 pt-5 pb-20 sm:px-8 md:pb-8">
              <div className="mx-auto max-w-[48rem]">
                <ArchivePane
                  dashboard={wellness}
                  loading={wellnessLoading}
                  jwOn={jwOn}
                  onRefresh={reloadWellness}
                  onUpdateCabin={(p) => void handleUpdateCabin(p)}
                  onAskInChat={(q) => {
                    setView("chat");
                    setPrefill((p) => ({ text: q, n: p.n + 1 }));
                  }}
                />
              </div>
            </main>
          )}

          {view === "connect" && (
            <main className="flex-1 overflow-y-auto px-4 pt-5 pb-20 sm:px-8 md:pb-8">
              <div className="mx-auto max-w-[48rem]">
                <ConnectPane
                  jwOn={jwOn}
                  isStaff={isStaff}
                  isAdmin={isAdmin}
                  onJwUpdated={reloadToday}
                  onWorkspaceSaved={loadWorkspace}
                />
              </div>
            </main>
          )}

          {/* 移动端底部单手导航栏 */}
          <nav
            aria-label="移动端主导航"
            className="grid grid-cols-3 border-t-2 border-ink bg-paper pb-[env(safe-area-inset-bottom)] md:hidden"
          >
            {([
              ["chat", "💬 对话", 0],
              ["archive", "🌿 情绪记忆", pendingTodos.length],
              ["connect", "🔗 连接", 0],
            ] as [CoreView, string, number][]).map(([id, label, badge]) => {
              const active = view === id;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setView(id)}
                  className={`flex items-center justify-center gap-1 py-2.5 font-display text-sm ${
                    active ? "bg-skin text-ink font-semibold" : "text-muted"
                  }`}
                >
                  <span>{label}</span>
                  {badge > 0 && (
                    <span className="rounded-full bg-pink px-1.5 text-[0.65rem] text-ink tabular-nums">
                      {badge}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        {/* 桌面答辩驾驶舱：直观呈现知情共情记忆 + MCP 拆解待办 + 减压舱状态 */}
        {view === "chat" && (
          <aside className="hidden w-80 shrink-0 flex-col gap-3 overflow-y-auto lg:flex">
            <div className="sketch bg-paper p-3.5 space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-display text-base">🔌 MCP 工具生态</span>
                <span className="rounded-full bg-flower/25 px-2 py-0.5 text-xs">
                  {wellness?.mcp.tools.length ?? 6} 个工具就绪
                </span>
              </div>
              <p className="text-xs text-muted">
                标准 stdio MCP Host：可在陪伴倾诉中动态调用微待办拆解、情绪轨迹手账与减压舱工具。
              </p>
            </div>

            <DecompressionCabin cabin={cabin} compact onUpdate={(p) => void handleUpdateCabin(p)} />

            <div className="sketch bg-paper p-3.5 space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="font-display text-base">✅ 当前微待办 ({pendingTodos.length})</span>
                <button type="button" onClick={() => setView("archive")} className="link-ink text-xs">
                  全部 / 曲线
                </button>
              </div>
              {pendingTodos.length === 0 ? (
                <p className="text-xs text-muted">
                  暂无待办。发一句焦虑吐槽给助手，看看它怎么通过 MCP 帮你切分小步～
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {pendingTodos.slice(0, 4).map((todo) => (
                    <li key={todo.id} className="flex items-start gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={false}
                        onChange={() => {
                          void toggleWellnessTodo(todo.id, true).then(() => {
                            window.dispatchEvent(
                              new CustomEvent(PET_COMPANION_EVENT, {
                                detail: { mood: "cheering", text: `打卡「${todo.title.slice(0, 10)}」!` },
                              }),
                            );
                            void reloadWellness();
                          });
                        }}
                        className="mt-0.5 size-3.5 shrink-0 accent-pink"
                      />
                      <span className="min-w-0 flex-1 leading-snug">
                        {todo.title}
                        <span className="ml-1 text-muted">({todo.est_minutes}m)</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {wellness?.avg_energy != null && (
              <div className="sketch bg-paper p-3.5 space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-display text-sm">🌤️ 近期情绪能量均值</span>
                  <strong className="text-sm tabular-nums">{wellness.avg_energy} / 10</strong>
                </div>
                {wellness.journals[0] && (
                  <p className="text-xs text-muted line-clamp-2">
                    最近「{wellness.journals[0].mood}」：{wellness.journals[0].summary}
                  </p>
                )}
              </div>
            )}
          </aside>
        )}
      </div>

      <SelectionAsk
        containerRef={scrollRef}
        onAsk={(text) => {
          setView("chat");
          setPrefill((p) => ({ text: quoteBlock(text), n: p.n + 1 }));
        }}
      />

      <SessionList
        open={historyOpen}
        sessions={chat.sessions}
        error={chat.sessionsError}
        currentId={threadId}
        onClose={closeHistory}
        onOpen={handleOpenSession}
        onNew={handleNewSession}
        onDelete={chat.removeSession}
      />

      <DeskPet
        thinking={busy}
        failed={Boolean(error) || answerFailed}
        onOpenSettings={() => setView("connect")}
      />
    </div>
  );
}
