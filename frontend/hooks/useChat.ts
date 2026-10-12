"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { migrateLegacyMemories, readSettings } from "@/lib/agent-settings";
import {
  type ChatMessage,
  type ChatStatus,
  addMemories,
  createSession,
  fetchMessages,
  streamChat,
} from "@/lib/api";

const STORAGE_KEY = "zhiyue.threadId";
const EMBED_STORAGE_KEY = "zhiyue.embed.threadId";

const STAGE_LABEL: Record<ChatStatus["stage"], string> = {
  intent: "正在理解你的问题…",
  retrieved: "正在翻阅知识库…",
  generating: "正在组织回答…",
  remembered: "已记下，正在回复…",
};

export interface UseChatOptions {
  /** 嵌入挂件：会话只存 sessionStorage、不改地址栏、不带自带密钥、不迁移记忆 */
  embedded?: boolean;
}

function newId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

/** thread_id 优先取 URL 上的 ?tid=，其次 localStorage，都没有才向后端申请。 */
function readStoredThreadId(embedded: boolean): string | null {
  if (embedded) return window.sessionStorage.getItem(EMBED_STORAGE_KEY);
  const fromUrl = new URLSearchParams(window.location.search).get("tid");
  if (fromUrl) return fromUrl;
  return window.localStorage.getItem(STORAGE_KEY);
}

function persistThreadId(threadId: string, embedded: boolean) {
  if (embedded) {
    window.sessionStorage.setItem(EMBED_STORAGE_KEY, threadId);
    return;
  }
  window.localStorage.setItem(STORAGE_KEY, threadId);
  const url = new URL(window.location.href);
  url.searchParams.set("tid", threadId);
  window.history.replaceState(null, "", url);
}

export function useChat(onAnswer?: (text: string) => void, options: UseChatOptions = {}) {
  const embedded = options.embedded ?? false;
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
        let tid = readStoredThreadId(embedded);
        if (!tid) {
          tid = (await createSession()).thread_id;
        }
        persistThreadId(tid, embedded);
        setThreadId(tid);

        const history = await fetchMessages(tid);
        setMessages(history.map((item) => ({ ...item, id: newId() })));
      } catch (err) {
        setError(err instanceof Error ? err.message : "无法连接后端服务");
      } finally {
        setRestoring(false);
      }
      if (!embedded) {
        migrateLegacyMemories(addMemories).catch(() => {
          // 迁移失败不影响聊天，旧记忆留在本机，下次打开再试
        });
      }
    })();
  }, [embedded]);

  const send = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || !threadId || abortRef.current) return;

      setError(null);
      setMessages((prev) => [...prev, { id: newId(), role: "user", content: text }]);
      setStatus(STAGE_LABEL.intent);

      const controller = new AbortController();
      abortRef.current = controller;

      const settings = embedded ? null : readSettings();
      const llm = settings?.apiKey
        ? {
            api_key: settings.apiKey,
            base_url: settings.baseUrl,
            model: settings.model,
            stream: settings.stream,
          }
        : undefined;

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
                  sources: result.sources,
                  emotion: result.emotion,
                },
              ]);
              setPending("");
              onAnswer?.(result.answer);
            },
            onError: (message) => setError(message),
          },
          controller.signal,
          llm,
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
    [threadId, onAnswer, embedded],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const startNewSession = useCallback(async () => {
    abortRef.current?.abort();
    try {
      const { thread_id: tid } = await createSession();
      persistThreadId(tid, embedded);
      setThreadId(tid);
      setMessages([]);
      setPending("");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "新建会话失败");
    }
  }, [embedded]);

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
