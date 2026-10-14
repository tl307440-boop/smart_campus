/** 把后端/网络的原始报错翻成学生看得懂的话；原文只留给管理员排查。 */

/**
 * 后端出错时把异常包成回答文本，不走 error 事件：
 * graph_app.generate_node（直接对话）与 llm_service.chat_with_context（按资料回答）各一种开头。
 */
const FALLBACK_PREFIXES = ["抱歉，我暂时无法回答这个问题。错误信息:", "deepseek 回复失败："] as const;

export type FailureKind = "service" | "network" | "session" | "busy" | "other";

export interface Failure {
  kind: FailureKind;
  title: string;
  hint: string;
}

const SERVICE = /error code: 40[13]|authentication|api[ _-]?key|unauthori[sz]ed|invalid_request_error|insufficient_quota|未配置|密钥/i;
const NETWORK = /network ?error|failed to fetch|networkerror|load failed|econnrefused|timed? ?out|无法连接|连接后端失败（(0|5\d\d)）/i;
const SESSION = /连接后端失败（401）|会话已过期|令牌/;
const BUSY = /连接后端失败（429）|too many|rate limit|上限|次数/i;

/** 后端在兜底回答里标出这次用的是谁的模型（backend/ai_config.py 的 SOURCE_LABEL） */
const SOURCE_MARKS = { personal: "（个人密钥）", school: "（学校配置）", platform: "（平台默认）" } as const;

export type ModelSource = keyof typeof SOURCE_MARKS | "none";

export function failedModelSource(raw: string): ModelSource | null {
  if (raw.includes("（未配置）")) return "none";
  for (const [source, mark] of Object.entries(SOURCE_MARKS) as [keyof typeof SOURCE_MARKS, string][]) {
    if (raw.includes(mark)) return source;
  }
  return null;
}

export function isFallbackAnswer(content: string): boolean {
  return FALLBACK_PREFIXES.some((prefix) => content.startsWith(prefix));
}

/** 流式输出中：已经是兜底回答，或者还看不出是不是（只吐了前缀的前几个字），都先不显示 */
export function mayBeFallback(partial: string): boolean {
  return FALLBACK_PREFIXES.some((prefix) => prefix.startsWith(partial) || partial.startsWith(prefix));
}

/**
 * 按原始报错归类，给出标题和下一步建议。
 *
 * Args:
 *   raw: 后端 error 事件、fetch 异常或兜底回答的原文。
 *   embedded: 嵌入挂件里的访客没有账号，过期时让他刷新页面。
 * Returns:
 *   Failure，文案不含任何原始报错。
 * Example:
 *   describeFailure("Error code: 401 - {...}").kind === "service"
 */
export function describeFailure(raw: string, embedded = false): Failure {
  if (SERVICE.test(raw)) {
    return {
      kind: "service",
      title: "AI 服务暂时连不上",
      hint: "学校的 AI 服务还没配置好或密钥失效了，请联系老师或管理员。",
    };
  }
  if (SESSION.test(raw)) {
    return {
      kind: "session",
      title: embedded ? "聊天窗口过期了" : "登录过期了",
      hint: embedded ? "刷新一下页面再问就好。" : "退出后重新登录一次再问。",
    };
  }
  if (NETWORK.test(raw)) {
    return { kind: "network", title: "网络好像断了", hint: "检查一下网络，稍后再试。" };
  }
  if (BUSY.test(raw)) {
    return { kind: "busy", title: "问得有点快", hint: "今天的提问次数可能用完了，过一会儿再试。" };
  }
  return { kind: "other", title: "这次没答上来", hint: "稍后再试一次，或者换个说法问问。" };
}
