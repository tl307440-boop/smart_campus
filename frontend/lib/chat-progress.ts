import type { ChatStatus, ChatToolEvent } from "@/lib/api";

/** 首字出来之前的等待阶段，由 SSE 的 status / tool 事件推进 */
export type ChatPhase = "thinking" | "reading" | "jw" | "news" | "web" | "map" | "tool" | "writing" | "remembered";

export interface ChatProgress {
  phase: ChatPhase;
  /** phase 为 tool 时显示后端给的工具名，如「查本校资料」 */
  label?: string;
  /** 发出去超过 SLOW_AFTER_MS 还没出第一个字 */
  slow: boolean;
}

export const SLOW_AFTER_MS = 8000;

const STAGE_PHASE: Record<ChatStatus["stage"], ChatPhase> = {
  intent: "thinking",
  retrieved: "reading",
  generating: "writing",
  remembered: "remembered",
};

export function phaseOfStatus(status: ChatStatus): ChatPhase {
  return STAGE_PHASE[status.stage] ?? "thinking";
}

export function phaseOfTool(event: ChatToolEvent): ChatPhase {
  if (event.name.startsWith("jw_")) return "jw";
  if (event.name === "school_news" || event.name === "read_notice") return "news";
  if (event.name === "web_search") return "web";
  if (event.name === "campus_place" || event.name === "walking_route") return "map";
  if (event.name === "search_knowledge") return "reading";
  return "tool";
}

/** 一句有人味的进度文案；助手名由学校在工作区设置里改 */
export function progressText(progress: ChatProgress, name: string): string {
  if (progress.slow) return "有点慢，再等等我…";
  const who = name || "我";
  switch (progress.phase) {
    case "thinking":
      return `${who}在想了…`;
    case "reading":
      return `${who}在翻学校的资料…`;
    case "jw":
      return `${who}在登教务系统…`;
    case "news":
      return `${who}在看学校通知…`;
    case "web":
      return `${who}在网上找…`;
    case "map":
      return `${who}在看地图…`;
    case "tool":
      return `${who}在${progress.label ?? "查资料"}…`;
    case "writing":
      return `${who}在写回答…`;
    case "remembered":
      return "记下了，马上回你…";
  }
}
