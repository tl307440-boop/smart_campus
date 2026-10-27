"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { migrateLegacyMemories, readSettings } from "@/lib/agent-settings";
import { type ChatProgress, phaseOfStatus, phaseOfTool, SLOW_AFTER_MS } from "@/lib/chat-progress";
import { isFallbackAnswer } from "@/lib/friendly-error";
import { persistThreadId, readStoredThreadId } from "@/lib/thread-storage";
import {
  type ChatDone,
  type ChatMessage,
  type ChatToolEvent,
  type FeedbackRating,
  type FeedbackReason,
  type SessionSummary,
  type StoredMessage,
  addMemories,
  createSession,
  deleteSession,
  fetchMessages,
  listSessions,
  rateMessage,
  SessionGoneError,
  streamChat,
} from "@/lib/api";

export interface UseChatOptions {
  /** 嵌入挂件：会话只存 sessionStorage、不改地址栏、不带自带密钥、不迁移记忆、没有会话列表 */
  embedded?: boolean;
}

/** 只发图没打字时的提问；与后端 vision.DEFAULT_QUESTION 保持一致 */
const PICTURE_QUESTION = "请看一下这张图片，告诉我上面是什么、有什么要注意的";

interface SendOptions {
  /** 替换最后一轮：重新生成时等于原提问，改了再问时是改之前的那句 */
  replace?: string;
  /** 附图（data URL）；重新生成时沿用最后一轮自己带的图 */
  images?: string[];
}

/** 朗读时接在回答后面，把求助电话也念出来；回答本身是报错时只念求助电话 */
function spokenAnswer(result: ChatDone): string {
  if (!result.safety) return result.answer;
  const school = result.safety.school ? `学校心理咨询：${result.safety.school}。` : "";
  if (result.answer.includes("12356")) {
    return result.answer.includes(result.safety.school) ? result.answer : `${result.answer}\n${school}`;
  }
  const answer = isFallbackAnswer(result.answer) ? "" : `${result.answer}\n`;
  return `${answer}需要帮助的话，可以拨打全国心理援助热线 12356，有立即危险请打 110 或 120。${school}`;
}

function newId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

/** 库里读出来的消息换成界面用的：前端自己编一个 id，库里的编号留作点赞点踩用（只有助手回答能评价） */
function fromStored({ id, feedback_reason: feedbackReason, ...rest }: StoredMessage): ChatMessage {
  return { ...rest, id: newId(), serverId: rest.role === "assistant" && id ? id : undefined, feedbackReason };
}

function lastUserIndex(messages: ChatMessage[]): number {
  return messages.findLastIndex((message) => message.role === "user");
}

/** 同名工具只留最后一次的状态：查了两次课表算一处 */
function mergeTool(tools: ChatToolEvent[], event: ChatToolEvent): ChatToolEvent[] {
  return [...tools.filter((tool) => tool.name !== event.name), event];
}

export function useChat(onAnswer?: (text: string) => void, options: UseChatOptions = {}) {
  const embedded = options.embedded ?? false;
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState("");
  const [progress, setProgress] = useState<ChatProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(true);
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [sessionsError, setSessionsError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const initialized = useRef(false);
  const messagesRef = useRef<ChatMessage[]>([]);
  const sessionsLoaded = useRef(false);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const freshSession = useCallback(async () => {
    const { thread_id: tid } = await createSession();
    persistThreadId(tid, embedded);
    setThreadId(tid);
    setMessages([]);
    return tid;
  }, [embedded]);

  /** 打开一个会话；它被删了或不是这个账号的，就换一页新的 */
  const load = useCallback(
    async (tid: string) => {
      try {
        const history = await fetchMessages(tid);
        persistThreadId(tid, embedded);
        setThreadId(tid);
        setMessages(history.map(fromStored));
      } catch (err) {
        if (!(err instanceof SessionGoneError)) throw err;
        await freshSession();
      }
    },
    [embedded, freshSession],
  );

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;

    (async () => {
      try {
        const stored = readStoredThreadId(embedded);
        if (stored) await load(stored);
        else await freshSession();
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
  }, [embedded, load, freshSession]);

  const refreshSessions = useCallback(async () => {
    if (embedded) return;
    try {
      setSessions(await listSessions());
      setSessionsError(null);
      sessionsLoaded.current = true;
    } catch (err) {
      setSessionsError(err instanceof Error ? err.message : "会话列表读取失败");
    }
  }, [embedded]);

  const send = useCallback(
    async (question: string, { replace, images }: SendOptions = {}) => {
      const attached = images?.length ? images : undefined;
      // 只发图没打字：给一句默认的话，库里存的也是这句
      const text = question.trim() || (attached ? PICTURE_QUESTION : "");
      if (!text || !threadId || abortRef.current) return;

      setError(null);
      setMessages((prev) => {
        const kept = replace === undefined ? prev : prev.slice(0, Math.max(lastUserIndex(prev), 0));
        return [...kept, { id: newId(), role: "user", content: text, images: attached }];
      });
      setProgress({ phase: "thinking", slow: false });

      const controller = new AbortController();
      abortRef.current = controller;
      const slowTimer = window.setTimeout(
        () => setProgress((current) => (current ? { ...current, slow: true } : current)),
        SLOW_AFTER_MS,
      );

      const settings = embedded ? null : readSettings();
      const llm = settings?.apiKey
        ? { api_key: settings.apiKey, base_url: settings.baseUrl, model: settings.model, stream: settings.stream }
        : undefined;

      let streamed = "";
      let tools: ChatToolEvent[] = [];
      const handlers = {
        onStatus: (status: Parameters<typeof phaseOfStatus>[0]) =>
          setProgress((current) => ({ phase: phaseOfStatus(status), slow: current?.slow ?? false })),
        onTool: (event: ChatToolEvent) => {
          tools = mergeTool(tools, event);
          // 查完不改回去：工具很快时「在登教务系统…」会一闪而过，留到回答开始吐字再换
          if (event.state === "running") {
            setProgress((current) => ({
              phase: phaseOfTool(event),
              label: event.label,
              etaMs: event.eta_ms,
              slow: current?.slow ?? false,
            }));
          }
        },
        onDelta: (chunk: string) => {
          window.clearTimeout(slowTimer);
          streamed += chunk;
          setPending(streamed);
        },
        onDone: (result: ChatDone) => {
          setMessages((prev) => [
            ...prev,
            {
              id: newId(),
              role: "assistant",
              content: result.answer,
              context: result.context,
              sources: result.sources,
              tables: result.tables ?? [],
              followUps: result.follow_ups ?? [],
              emotion: result.emotion,
              safety: result.safety,
              tools,
              serverId: result.message_id,
            },
          ]);
          setPending("");
          onAnswer?.(spokenAnswer(result));
          if (sessionsLoaded.current) void refreshSessions();
        },
        onError: (message: string) => setError(message),
      };

      try {
        try {
          await streamChat(threadId, text, handlers, controller.signal, llm, replace, attached);
        } catch (err) {
          if (!(err instanceof SessionGoneError)) throw err;
          // 这一页在别处被删掉了：换一页新的，把这句话原样再问一次
          const tid = await freshSession();
          setMessages([{ id: newId(), role: "user", content: text, images: attached }]);
          await streamChat(tid, text, handlers, controller.signal, llm, undefined, attached);
        }
      } catch (err) {
        // 用户主动中断不算错误
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          setError(err instanceof Error ? err.message : "回答生成失败");
        }
        if (streamed) {
          setMessages((prev) => [...prev, { id: newId(), role: "assistant", content: streamed, tools }]);
        }
        setPending("");
      } finally {
        window.clearTimeout(slowTimer);
        abortRef.current = null;
        setProgress(null);
      }
    },
    [threadId, onAnswer, embedded, freshSession, refreshSessions],
  );

  /** 用最后一轮的提问再答一次，替换掉原来的回答（或报错） */
  const regenerate = useCallback(() => {
    const last = messagesRef.current[lastUserIndex(messagesRef.current)];
    if (!last) return;
    if (last.content === PICTURE_QUESTION && !last.images) {
      // 图片不入库，刷新或换会话后就没了；没图可看就别拿空问题去答
      setError("这张图片没有保存，请重新附上图片再问。");
      return;
    }
    void send(last.content, { replace: last.content, images: last.images });
  }, [send]);

  /** 改了最后一句提问再问：原来那一轮整轮作废 */
  const editLast = useCallback(
    (text: string) => {
      const last = messagesRef.current[lastUserIndex(messagesRef.current)];
      if (last) void send(text, { replace: last.content, images: last.images });
    },
    [send],
  );

  /** 给一条回答点赞 / 点踩（0 取消）。先改界面，没记上就改回去并把错抛给调用方 */
  const rate = useCallback(async (id: string, rating: FeedbackRating, reason: FeedbackReason = "") => {
    const target = messagesRef.current.find((message) => message.id === id);
    if (!target?.serverId) return;
    const before = { feedback: target.feedback ?? 0, feedbackReason: target.feedbackReason ?? "" };
    const patch = (value: Pick<ChatMessage, "feedback" | "feedbackReason">) =>
      setMessages((prev) => prev.map((message) => (message.id === id ? { ...message, ...value } : message)));
    patch({ feedback: rating, feedbackReason: rating === -1 ? reason : "" });
    try {
      await rateMessage(target.serverId, rating, reason);
    } catch (err) {
      patch(before);
      throw err;
    }
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const startNewSession = useCallback(async () => {
    abortRef.current?.abort();
    try {
      await freshSession();
      setPending("");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "新建会话失败");
    }
  }, [freshSession]);

  const openSession = useCallback(
    async (tid: string) => {
      if (tid === threadId) return;
      abortRef.current?.abort();
      setRestoring(true);
      setPending("");
      setError(null);
      try {
        await load(tid);
      } catch (err) {
        setError(err instanceof Error ? err.message : "没打开这个会话");
      } finally {
        setRestoring(false);
      }
    },
    [threadId, load],
  );

  const removeSession = useCallback(
    async (tid: string) => {
      await deleteSession(tid);
      setSessions((list) => list?.filter((item) => item.thread_id !== tid) ?? list);
      if (tid === threadId) await startNewSession();
    },
    [threadId, startNewSession],
  );

  return {
    threadId,
    messages,
    pending,
    progress,
    error,
    restoring,
    busy: progress !== null || pending !== "",
    sessions,
    sessionsError,
    send,
    regenerate,
    editLast,
    rate,
    stop,
    startNewSession,
    openSession,
    removeSession,
    refreshSessions,
  };
}
