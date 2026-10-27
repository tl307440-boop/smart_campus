"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { errorDetail, fetchTodayCard, type TodayCardData } from "@/lib/api";
import { localDay } from "@/lib/today";

const DAY_CHECK_MS = 60_000;

export type TodayState =
  | { status: "loading" }
  | { status: "ready"; card: TodayCardData }
  | { status: "error"; message: string };

/**
 * 今天的课和临近的考试。页面一直开着过了零点、或者隔很久切回来时，换成新的一天。
 * 放在页面层而不是卡片里：开始聊天后卡片会收起，课前提醒不能跟着停。
 */
export function useToday(): { state: TodayState; reload: () => void } {
  const [state, setState] = useState<TodayState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const loadedDay = useRef<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const day = localDay(new Date());
    fetchTodayCard(day, controller.signal)
      .then((card) => {
        loadedDay.current = day;
        setState({ status: "ready", card });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setState({ status: "error", message: errorDetail(err) });
      });
    return () => controller.abort();
  }, [attempt]);

  /** 已经有卡片时悄悄换新的，不让它闪回「正在看」 */
  const reload = useCallback(() => {
    setState((current) => (current.status === "ready" ? current : { status: "loading" }));
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    const newDay = () => {
      if (document.visibilityState !== "visible" || !loadedDay.current || loadedDay.current === localDay(new Date())) return;
      loadedDay.current = null; // 等这次取回来再比，别每分钟重复取
      reload();
    };
    const timer = window.setInterval(newDay, DAY_CHECK_MS);
    document.addEventListener("visibilitychange", newDay);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", newDay);
    };
  }, [reload]);

  return { state, reload };
}
