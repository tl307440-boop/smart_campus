"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MemoryPane, ServicePane } from "@/components/AgentSettings";
import { ChatHeader } from "@/components/ChatHeader";
import { Composer } from "@/components/Composer";
import { DeskPet } from "@/components/DeskPet";
import { EmbedPane } from "@/components/EmbedAdmin";
import { ErrorNote } from "@/components/ErrorNote";
import { KnowledgePane } from "@/components/KnowledgePane";
import { MessageBubble } from "@/components/MessageBubble";
import { SuggestedQuestions } from "@/components/SuggestedQuestions";
import { type ToolTab, ToolDrawer } from "@/components/ToolDrawer";
import { WorkspacePane } from "@/components/WorkspaceAdmin";
import { PET_LOOK_EVENT } from "@/hooks/useDeskPet";
import { readSettings } from "@/lib/agent-settings";
import { fetchWorkspace } from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { canManageKnowledge } from "@/lib/auth/session";
import { isFallbackAnswer, mayBeFallback } from "@/lib/friendly-error";
import { useChat } from "@/hooks/useChat";

const DEFAULT_ASSISTANT_NAME = "校园助手";

export default function ChatPage() {
  const { user, logout } = useAuth();
  const isStaff = canManageKnowledge(user?.role);
  const isAdmin = user?.role === "ADMIN";
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const [drawer, setDrawer] = useState<{ tab: ToolTab; open: boolean; n: number }>({ tab: "service", open: false, n: 0 });
  const [assistantName, setAssistantName] = useState<string | null>(null);

  const loadWorkspace = useCallback(() => {
    fetchWorkspace()
      .then((workspace) => setAssistantName(workspace.assistant_name || DEFAULT_ASSISTANT_NAME))
      .catch(() => setAssistantName((name) => name ?? DEFAULT_ASSISTANT_NAME));
  }, []);
  useEffect(loadWorkspace, [loadWorkspace]);
  const name = assistantName ?? "";

  const speak = useCallback(
    (text: string) => {
      if (!ttsEnabled || isFallbackAnswer(text)) return;
      const synth = window.speechSynthesis;
      if (!synth) return;
      synth.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "zh-CN";
      synth.speak(utterance);
    },
    [ttsEnabled],
  );

  const { messages, pending, status, error, restoring, busy, threadId, send, stop, startNewSession } =
    useChat(speak);
  const last = messages.at(-1);
  const answerFailed = last?.role === "assistant" && isFallbackAnswer(last.content);
  const signedId = !busy && last?.role === "assistant" && !answerFailed ? last.id : null;
  // 兜底回答也会被流式吐出来，先不显示，避免原始报错闪一下
  const pendingShown = pending && !mayBeFallback(pending) ? pending : "";

  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending, status]);

  const openTool = useCallback((tab: ToolTab) => setDrawer((d) => ({ tab, open: true, n: d.open ? d.n : d.n + 1 })), []);
  const closeTool = useCallback(() => setDrawer((d) => ({ ...d, open: false })), []);
  const openService = useCallback(() => openTool("service"), [openTool]);

  function handleSend(text: string) {
    void send(text);
    // 等新提问画到纸上，再让桌宠转头看它
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const asked = document.querySelectorAll("[data-role=user]");
        const rect = asked[asked.length - 1]?.getBoundingClientRect();
        if (rect) window.dispatchEvent(new CustomEvent(PET_LOOK_EVENT, { detail: { x: rect.right - 40, y: rect.top + rect.height / 2 } }));
      }),
    );
  }

  function handleToggleTts(enabled: boolean) {
    setTtsEnabled(enabled);
    if (!enabled) window.speechSynthesis?.cancel();
  }

  function handleNewSession() {
    window.speechSynthesis?.cancel();
    void startNewSession();
  }

  function handleLogout() {
    window.speechSynthesis?.cancel();
    logout();
  }

  const tabs: ToolTab[] = ["service", "memory", ...(isStaff ? (["knowledge"] as const) : []), ...(isAdmin ? (["workspace", "embeds"] as const) : [])];
  const ownKey = Boolean(readSettings().apiKey);
  const noteProps = { canDebug: isStaff, ownKey, onOpenService: openService };

  return (
    <div className="h-dvh lg:py-4">
      <div className="sketch-lg relative mx-auto flex h-full max-w-[48rem] flex-col bg-paper">
        <ChatHeader
          assistantName={name}
          user={user}
          isStaff={isStaff}
          threadId={threadId}
          ttsEnabled={ttsEnabled}
          onToggleTts={handleToggleTts}
          onNewSession={handleNewSession}
          onOpenTools={() => openTool(drawer.tab)}
          onLogout={handleLogout}
        />

        <main className="flex-1 overflow-y-auto">
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
                    <SuggestedQuestions disabled={busy || !threadId} onAsk={handleSend} />
                  </div>
                )}

                {messages.map((message) =>
                  message.role === "assistant" && isFallbackAnswer(message.content) ? (
                    <ErrorNote key={message.id} raw={message.content} {...noteProps} />
                  ) : (
                    <MessageBubble key={message.id} message={message} signed={message.id === signedId} showDebug={isStaff} />
                  ),
                )}

                {pendingShown && (
                  <MessageBubble message={{ id: "pending", role: "assistant", content: pendingShown }} streaming />
                )}

                {status && !pendingShown && (
                  <p className="animate-fade-in flex items-center gap-2 text-[0.9375rem] text-muted" role="status">
                    <span className="flex gap-1" aria-hidden="true">
                      {[0, 1, 2].map((i) => (
                        <i key={i} className="pencil-bob size-1.5 rounded-full border border-ink bg-pollen" style={{ animationDelay: `${i * 120}ms` }} />
                      ))}
                    </span>
                    {status}
                  </p>
                )}
              </>
            )}

            {error && !answerFailed && <ErrorNote raw={error} {...noteProps} />}
            <div ref={bottomRef} />
          </div>
        </main>

        <div className="border-t-2 border-ink px-4 py-3 sm:px-8">
          <div className="mx-auto max-w-[45rem]">
            <Composer disabled={restoring || !threadId} busy={busy} onSend={handleSend} onStop={stop} />
          </div>
        </div>
      </div>

      <ToolDrawer tabs={tabs} tab={drawer.tab} open={drawer.open} onOpen={openTool} onClose={closeTool}>
        <div key={`${drawer.tab}-${drawer.n}`}>
          {drawer.tab === "service" && <ServicePane />}
          {drawer.tab === "memory" && <MemoryPane />}
          {drawer.tab === "knowledge" && isStaff && <KnowledgePane />}
          {drawer.tab === "workspace" && isAdmin && <WorkspacePane onSaved={loadWorkspace} />}
          {drawer.tab === "embeds" && isAdmin && <EmbedPane />}
        </div>
      </ToolDrawer>
      <DeskPet thinking={busy} failed={Boolean(error) || answerFailed} onOpenSettings={openService} />
    </div>
  );
}
