"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AgentSettings } from "@/components/AgentSettings";
import { Composer } from "@/components/Composer";
import { ConsolePanel } from "@/components/ConsolePanel";
import { DeskPet } from "@/components/DeskPet";
import { MessageBubble } from "@/components/MessageBubble";
import { WorkspaceAdmin } from "@/components/WorkspaceAdmin";
import { fetchWorkspace } from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { useChat } from "@/hooks/useChat";

export default function ChatPage() {
  const { user, logout } = useAuth();
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [assistantName, setAssistantName] = useState("小懒");

  useEffect(() => {
    fetchWorkspace()
      .then((workspace) => setAssistantName(workspace.assistant_name))
      .catch(() => {
        // 读不到就沿用默认称呼
      });
  }, [adminOpen]);

  const welcome = `同学，你好！我是你的校园助手${assistantName}。请问有什么可以帮你？`;

  const speak = useCallback(
    (text: string) => {
      if (!ttsEnabled) return;
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
  // 后端 generate_node 出错时把异常包成这句开头的回答（backend/graph_app.py），不走 error 事件
  const answerFailed = messages.at(-1)?.role === "assistant" && messages.at(-1)?.content.startsWith("抱歉，我暂时无法回答");

  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending, status]);

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

  return (
    <div className="flex h-dvh">
      <ConsolePanel
        threadId={threadId}
        ttsEnabled={ttsEnabled}
        onToggleTts={handleToggleTts}
        onNewSession={handleNewSession}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenAdmin={() => setAdminOpen(true)}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3 border-b border-line px-6 py-4">
          <div />
          <div className="text-center">
            <h1 className="font-display text-2xl font-bold tracking-[0.08em]">
              智悦校园 · <span className="text-gold">{assistantName}</span>
            </h1>
            <p className="mt-1 text-sm text-muted">
              校园问答助手。问规章、查资助、解疑惑——像在灯下和同学聊聊。
            </p>
          </div>
          <div className="justify-self-end text-right">
            {user && (
              <>
                <p className="truncate text-xs text-muted" title={user.studentNo}>
                  {user.name}
                </p>
                <button
                  type="button"
                  onClick={handleLogout}
                  className="mt-1 text-sm text-gold transition hover:underline"
                >
                  退出登录
                </button>
              </>
            )}
          </div>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            {restoring ? (
              <p className="text-center text-sm text-muted">正在恢复会话…</p>
            ) : (
              <>
                {messages.length === 0 && (
                  <MessageBubble
                    message={{ id: "welcome", role: "assistant", content: welcome }}
                    assistantName={assistantName}
                  />
                )}

                {messages.map((message) => (
                  <MessageBubble key={message.id} message={message} assistantName={assistantName} />
                ))}

                {pending && (
                  <MessageBubble
                    assistantName={assistantName}
                    message={{ id: "pending", role: "assistant", content: pending }}
                    streaming
                  />
                )}

                {status && !pending && (
                  <p className="animate-fade-in text-sm text-muted">{status}</p>
                )}
              </>
            )}

            {error && (
              <p className="rounded-xl border border-danger/40 bg-surface px-4 py-3 text-sm text-danger">
                {error}
              </p>
            )}

            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-line px-6 py-4">
          <div className="mx-auto max-w-3xl">
            <Composer disabled={restoring || !threadId} busy={busy} onSend={send} onStop={stop} />
          </div>
        </div>
      </main>
      <DeskPet thinking={busy} failed={Boolean(error) || answerFailed} onOpenSettings={() => setSettingsOpen(true)} />
      <AgentSettings open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <WorkspaceAdmin open={adminOpen} onClose={() => setAdminOpen(false)} />
    </div>
  );
}
