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

/** 同一阶段换了工具也算新的一步：先查课表再找地点是两步 */
export function stepKey(progress: ChatProgress): string {
  return progress.phase === "tool" || progress.phase === "jw" ? `${progress.phase}:${progress.label ?? ""}` : progress.phase;
}

/**
 * 进度条在这一阶段最多爬到哪儿（百分比）。后端不报真实进度，
 * 只能按阶段估一个上限，永远到不了 100，免得卡在满格上干等。
 */
const PHASE_CEILING: Record<ChatPhase, number> = {
  thinking: 24,
  reading: 52,
  jw: 60,
  news: 60,
  web: 60,
  map: 60,
  tool: 60,
  remembered: 72,
  writing: 90,
};

export function progressCeiling(phase: ChatPhase, doneSteps: number): number {
  return Math.min(94, PHASE_CEILING[phase] + 6 * Math.max(0, doneSteps - 1));
}

/** 走过的步骤留在进度条下面打个勾 */
export function doneText(progress: ChatProgress): string {
  switch (progress.phase) {
    case "thinking":
      return "想明白了";
    case "reading":
      return "翻过资料";
    case "jw":
      return progress.label ? progress.label.replace(/^查/, "查完") : "登上教务";
    case "news":
      return "看过通知";
    case "web":
      return "搜过网上";
    case "map":
      return "看过地图";
    case "tool":
      return progress.label ?? "查过了";
    case "writing":
      return "写好了";
    case "remembered":
      return "记下了";
  }
}

const JW_WHISPERS: Record<string, string[]> = {
  查空教室: ["一间间教室看过去", "挑出没人占的那几间", "顺便看看离你近不近"],
  查课表: ["翻到这周的课表", "对一下上课时间和教室"],
  查考试安排: ["找考场和座位号", "按时间排一排先后"],
  查成绩: ["一门门核对成绩", "顺手算算绩点"],
  查毕业进度: ["数一数修了多少学分", "对照培养计划看看还差啥"],
};

const PHASE_WHISPERS: Record<ChatPhase, string[]> = {
  thinking: ["把你的问题读两遍", "想想答案藏在哪儿", "理一理思路"],
  reading: ["翻学生手册中", "对一下规章原文", "把重点划出来"],
  jw: ["敲开教务系统的门", "教务系统有点慢，排个队", "拿到数据马上回来"],
  news: ["翻官网公告栏", "看看教务处的新消息"],
  web: ["上网搜一搜", "多看几个来源对一对"],
  map: ["在地图上找位置", "量一量要走多远"],
  tool: ["认真查着呢", "拿到结果马上回来"],
  writing: ["组织一下语言", "马上就好"],
  remembered: ["写进小本本", "马上回你"],
};

const SLOW_WHISPERS = ["有点慢，再等等我", "网络有点堵，我还在", "别走开，快好了"];

/** 进度条下面轮换的小字，让等待有点事可看 */
export function whispers(progress: ChatProgress): string[] {
  const own = (progress.phase === "jw" && progress.label && JW_WHISPERS[progress.label]) || PHASE_WHISPERS[progress.phase];
  return progress.slow ? [...SLOW_WHISPERS, ...own] : own;
}

/** 一句有人味的进度文案；助手名由学校在工作区设置里改 */
export function progressText(progress: ChatProgress, name: string): string {
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
