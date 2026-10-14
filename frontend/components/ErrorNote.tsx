import { SafetyCard } from "@/components/SafetyCard";
import type { SafetyCardData } from "@/lib/api";
import { describeFailure, failedModelSource } from "@/lib/friendly-error";

interface Props {
  /** 原始报错：后端 error 事件、fetch 异常或兜底回答全文 */
  raw: string;
  embedded?: boolean;
  /** 管理员/老师：显示排查方向和可折叠的原始报错 */
  canDebug?: boolean;
  /** 管理员：能去「AI 服务」页改学校的模型配置 */
  canConfigure?: boolean;
  onOpenService?: () => void;
  /** 这一轮回答没出来，但情绪分析判出了高风险：求助卡片照样要给 */
  safety?: SafetyCardData | null;
}

const DEBUG_HINT = {
  personal: "",
  school: "学校在「AI 服务」页配的模型密钥或模型 ID 不对，去那里改好后点「测一下」。",
  platform: "服务器 .env 里的平台默认密钥（deepseek_API_key）无效；也可以在「AI 服务」页给学校单独配一把。",
  none: "个人、学校、平台都没有可用的对话模型：在「AI 服务」页填好学校的模型密钥。",
} as const;

/** 出错时的小纸条：学生只看到发生了什么、该找谁；原文折在「技术细节」里且只给管理员。 */
export function ErrorNote({ raw, embedded = false, canDebug = false, canConfigure = false, onOpenService, safety }: Props) {
  const failure = describeFailure(raw, embedded);
  const source = failure.kind === "service" ? failedModelSource(raw) : null;
  // 报错里带着「（个人密钥）」：确实是本人自带的那把出了问题，不是学校的
  const ownKeyProblem = source === "personal" && !embedded;
  let hint = failure.hint;
  if (ownKeyProblem) hint = "你在「AI 服务」里填的密钥好像不对。换一把再试，或者清空密钥改用学校的服务。";
  else if (source && canDebug) hint = DEBUG_HINT[source];
  const action = ownKeyProblem ? "去 AI 服务换一把" : source && canConfigure ? "去 AI 服务看看" : null;

  return (
    <div role="alert" className="animate-fade-in flex items-start gap-3">
      <span
        className="sketch grid size-9 shrink-0 place-items-center rounded-full bg-skin font-display text-xl text-pink"
        aria-hidden="true"
      >
        !
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <p className="font-display text-lg leading-snug">{failure.title}</p>
        <p className="mt-0.5 text-[0.9375rem] text-muted">{hint}</p>
        {action && onOpenService && (
          <button type="button" onClick={onOpenService} className="btn btn-sm mt-2.5">
            {action}
          </button>
        )}
        {canDebug && (
          <details className="mt-2 text-sm text-muted">
            <summary className="cursor-pointer">技术细节</summary>
            <pre className="mt-1.5 max-h-48 overflow-auto font-body text-[0.8125rem] whitespace-pre-wrap break-all">
              {raw}
            </pre>
          </details>
        )}
        {safety && <SafetyCard card={safety} />}
      </div>
    </div>
  );
}
