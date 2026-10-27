"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MemoryPane, ServicePane } from "@/components/AgentSettings";
import { ChatHeader } from "@/components/ChatHeader";
import { ChatLog } from "@/components/ChatLog";
import { Composer, type ComposerPrefill } from "@/components/Composer";
import { DeskPet } from "@/components/DeskPet";
import { EmbedPane } from "@/components/EmbedAdmin";
import { JwAccountPane } from "@/components/JwAccountPane";
import { KnowledgePane } from "@/components/KnowledgePane";
import { SelectionAsk } from "@/components/SelectionAsk";
import { SessionList } from "@/components/SessionList";
import { ArrowDownIcon } from "@/components/SketchIcons";
import { SuggestedQuestions } from "@/components/SuggestedQuestions";
import { TodayCard } from "@/components/TodayCard";
import { useToast } from "@/components/Toast";
import { type ToolTab, ToolDrawer } from "@/components/ToolDrawer";
import { WorkspacePane } from "@/components/WorkspaceAdmin";
import { PET_LOOK_EVENT } from "@/hooks/useDeskPet";
import { useLessonReminders } from "@/hooks/useLessonReminders";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { useToday } from "@/hooks/useToday";
import { fetchWorkspace } from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { canManageKnowledge } from "@/lib/auth/session";
import { isFallbackAnswer } from "@/lib/friendly-error";
import { useReminderSettings } from "@/lib/reminder-settings";
import { speakText, useSpeechSettings } from "@/lib/speech-settings";
import { useChat } from "@/hooks/useChat";

const DEFAULT_ASSISTANT_NAME = "校园助手";

/** 「就这段再问」：选中的文字变成引用块，后面空一行给学生接着写 */
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
  const { notice } = useToast();
  const { state: today, reload: reloadToday } = useToday();
  const [reminder, setReminder] = useReminderSettings();
  // 提醒挂在页面上而不是卡片上：开始聊天后卡片会收起，该提醒的课还是要提醒
  useLessonReminders(today.status === "ready" ? today.card : null, reminder, notice);
  const [drawer, setDrawer] = useState<{ tab: ToolTab; open: boolean; n: number }>({ tab: "service", open: false, n: 0 });
  const [historyOpen, setHistoryOpen] = useState(false);
  const [assistantName, setAssistantName] = useState<string | null>(null);
  const [prefill, setPrefill] = useState<ComposerPrefill>({ text: "", n: 0 });

  const loadWorkspace = useCallback(() => {
    fetchWorkspace()
      .then((workspace) => setAssistantName(workspace.assistant_name || DEFAULT_ASSISTANT_NAME))
      .catch(() => setAssistantName((name) => name ?? DEFAULT_ASSISTANT_NAME));
  }, []);
  useEffect(loadWorkspace, [loadWorkspace]);
  const name = assistantName ?? "";

  const speak = useCallback(
    (text: string) => {
      if (speech.enabled && !isFallbackAnswer(text)) speakText(text, speech);
    },
    [speech],
  );

  const chat = useChat(speak);
  const { messages, pending, progress, error, restoring, busy, threadId } = chat;
  const last = messages.at(-1);
  const answerFailed = last?.role === "assistant" && isFallbackAnswer(last.content);
  const lastQuestion = messages.findLast((message) => message.role === "user")?.content;

  const scrollRef = useRef<HTMLElement>(null);
  const { atBottom, unseen, scrollToBottom } = useStickToBottom(scrollRef, `${messages.length}:${pending.length}:${progress?.phase}`);

  const openTool = useCallback((tab: ToolTab) => setDrawer((d) => ({ tab, open: true, n: d.open ? d.n : d.n + 1 })), []);
  const closeTool = useCallback(() => {
    setDrawer((d) => ({ ...d, open: false }));
    // 刚在「教务账号」里绑定、换绑或解绑了：卡片跟着换
    if (drawer.tab === "jw") reloadToday();
  }, [drawer.tab, reloadToday]);
  const openService = useCallback(() => openTool("service"), [openTool]);
  const closeHistory = useCallback(() => setHistoryOpen(false), []);

  function handleSend(text: string) {
    void chat.send(text);
    // 等新提问画到纸上，再让桌宠转头看它
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const asked = document.querySelectorAll("[data-role=user]");
        const rect = asked[asked.length - 1]?.getBoundingClientRect();
        if (rect) window.dispatchEvent(new CustomEvent(PET_LOOK_EVENT, { detail: { x: rect.right - 40, y: rect.top + rect.height / 2 } }));
      }),
    );
  }

  function handleNewSession() {
    window.speechSynthesis?.cancel();
    setHistoryOpen(false);
    void chat.startNewSession();
  }

  function handleOpenHistory() {
    setHistoryOpen(true);
    void chat.refreshSessions();
  }

  function handleOpenSession(tid: string) {
    window.speechSynthesis?.cancel();
    setHistoryOpen(false);
    void chat.openSession(tid);
  }

  function handleLogout() {
    window.speechSynthesis?.cancel();
    logout();
  }

  const tabs: ToolTab[] = ["service", "memory", "jw", ...(isStaff ? (["knowledge"] as const) : []), ...(isAdmin ? (["workspace", "embeds"] as const) : [])];
  const noteProps = { canDebug: isStaff, canConfigure: isAdmin, onOpenService: openService };

  return (
    <div className="h-dvh lg:py-4">
      <div className="sketch-lg relative mx-auto flex h-full max-w-[48rem] flex-col bg-paper">
        <ChatHeader
          assistantName={name}
          user={user}
          isStaff={isStaff}
          threadId={threadId}
          onNewSession={handleNewSession}
          onOpenHistory={handleOpenHistory}
          onOpenTools={() => openTool(drawer.tab)}
          onLogout={handleLogout}
        />

        <main ref={scrollRef} className="relative flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-[45rem] flex-col gap-7 px-5 pt-8 pb-36 sm:px-8 lg:pb-10">
            {restoring ? (
              <p className="text-center text-muted">正在翻到上次那一页…</p>
            ) : (
              <>
                {messages.length === 0 && (
                  <div className="rise-in space-y-6">
                    <div>
                      <h1 className="font-display text-[2rem] leading-tight sm:text-[2.5rem]">嗨，我是{name}</h1>
                      <p className="mt-2 text-muted">规章、资助、考试和校园生活，直接问我就好。回答下面会附上出处。</p>
                    </div>
                    <TodayCard
                      state={today}
                      reminder={reminder}
                      onReminderChange={setReminder}
                      onRetry={reloadToday}
                      onBind={() => openTool("jw")}
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
            className="btn btn-icon pop-in absolute right-5 bottom-24 z-10 rounded-full shadow-[2px_3px_0_rgba(27,18,20,0.12)] sm:right-8"
          >
            <ArrowDownIcon size={18} />
            {unseen && <span className="absolute -top-0.5 -right-0.5 size-2.5 rounded-full border border-ink bg-pink" aria-hidden="true" />}
          </button>
        )}

        <div className="border-t-2 border-ink px-4 py-3 sm:px-8">
          <div className="mx-auto max-w-[45rem]">
            <Composer
              disabled={restoring || !threadId}
              busy={busy}
              onSend={handleSend}
              onStop={chat.stop}
              prefill={prefill}
              lastQuestion={lastQuestion}
            />
          </div>
        </div>
      </div>

      <SelectionAsk containerRef={scrollRef} onAsk={(text) => setPrefill((p) => ({ text: quoteBlock(text), n: p.n + 1 }))} />

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

      <ToolDrawer tabs={tabs} tab={drawer.tab} open={drawer.open} onOpen={openTool} onClose={closeTool}>
        <div key={`${drawer.tab}-${drawer.n}`}>
          {drawer.tab === "service" && <ServicePane isAdmin={isAdmin} />}
          {drawer.tab === "memory" && <MemoryPane />}
          {drawer.tab === "jw" && <JwAccountPane />}
          {drawer.tab === "knowledge" && isStaff && <KnowledgePane />}
          {drawer.tab === "workspace" && isAdmin && <WorkspacePane onSaved={loadWorkspace} />}
          {drawer.tab === "embeds" && isAdmin && <EmbedPane />}
        </div>
      </ToolDrawer>
      <DeskPet thinking={busy} failed={Boolean(error) || answerFailed} onOpenSettings={openService} />
    </div>
  );
}
