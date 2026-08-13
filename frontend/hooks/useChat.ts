"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type ChatMessage,
  type ChatStatus,
  createSession,
  fetchMessages,
  streamChat,
} from "@/lib/api";

const STORAGE_KEY = "zhiyue.threadId";

const STAGE_LABEL: Record<ChatStatus["stage"], string> = {
  intent: "小懒正在理解你的问题…",
  retrieved: "正在翻阅知识库…",
  generating: "正在组织回答…",
};

function newId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

/** thread_id 优先取 URL 上的 ?tid=，其次 localStorage，都没有才向后端申请。 */
function readStoredThreadId(): string | null {
  const fromUrl = new URLSearchParams(window.location.search).get("tid");
  if (fromUrl) return fromUrl;
  return window.localStorage.getItem(STORAGE_KEY);
}

function persistThreadId(threadId: string) {
  window.localStorage.setItem(STORAGE_KEY, threadId);
  const url = new URL(window.location.href);
  url.searchParams.set("tid", threadId);
  window.history.replaceState(null, "", url);
}

export function useChat(onAnswer?: (text: string) => void) {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(true);

  const abortRef = useRef<AbortController | null>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;

    (async () => {
      try {
        let tid = readStoredThreadId();
        if (!tid) {
          tid = (await createSession()).thread_id;
        }
        persistThreadId(tid);
        setThreadId(tid);

        const history = await fetchMessages(tid);
        setMessages(history.map((item) => ({ ...item, id: newId() })));
      } catch (err) {
        setError(err instanceof Error ? err.message : "无法连接后端服务");
      } finally {
        setRestoring(false);
      }
    })();
  }, []);

  const send = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || !threadId || abortRef.current) return;

      setError(null);
      setMessages((prev) => [...prev, { id: newId(), role: "user", content: text }]);
      setStatus(STAGE_LABEL.intent);

      const controller = new AbortController();
      abortRef.current = controller;

      let streamed = "";
      try {
        await streamChat(
          threadId,
          text,
          {
            onStatus: (next) => setStatus(STAGE_LABEL[next.stage] ?? null),
            onDelta: (chunk) => {
              streamed += chunk;
              setPending(streamed);
            },
            onDone: (result) => {
              setMessages((prev) => [
                ...prev,
                {
                  id: newId(),
                  role: "assistant",
                  content: result.answer,
                  context: result.context,
                  emotion: result.emotion,
                },
              ]);
              setPending("");
              onAnswer?.(result.answer);
            },
            onError: (message) => setError(message),
          },
          controller.signal,
        );
      } catch (err) {
        // 用户主动中断不算错误
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          setError(err instanceof Error ? err.message : "回答生成失败");
        }
        if (streamed) {
          setMessages((prev) => [
            ...prev,
            { id: newId(), role: "assistant", content: streamed },
          ]);
        }
        setPending("");
      } finally {
        abortRef.current = null;
        setStatus(null);
      }
    },
    [threadId, onAnswer],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const startNewSession = useCallback(async () => {
    abortRef.current?.abort();
    try {
      const { thread_id: tid } = await createSession();
      persistThreadId(tid);
      setThreadId(tid);
      setMessages([]);
      setPending("");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "新建会话失败");
    }
  }, []);

  return {
    threadId,
    messages,
    pending,
    status,
    error,
    restoring,
    busy: status !== null || pending !== "",
    send,
    stop,
    startNewSession,
  };
}
