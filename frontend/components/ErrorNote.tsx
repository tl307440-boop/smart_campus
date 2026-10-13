import { describeFailure } from "@/lib/friendly-error";

interface Props {
  /** 原始报错：后端 error 事件、fetch 异常或兜底回答全文 */
  raw: string;
  embedded?: boolean;
  /** 管理员/老师：显示排查方向和可折叠的原始报错 */
  canDebug?: boolean;
  /** 当前浏览器填了自带密钥：密钥问题出在自己这边 */
  ownKey?: boolean;
  onOpenService?: () => void;
}

/** 出错时的小纸条：学生只看到发生了什么、该找谁；原文折在「技术细节」里且只给管理员。 */
export function ErrorNote({ raw, embedded = false, canDebug = false, ownKey = false, onOpenService }: Props) {
  const failure = describeFailure(raw, embedded);
  const ownKeyProblem = failure.kind === "service" && ownKey && !embedded;
  let hint = failure.hint;
  if (ownKeyProblem) hint = "你在「AI 服务」里填的密钥好像不对。换一把再试，或者清空密钥改用学校的服务。";
  else if (failure.kind === "service" && canDebug) hint = "服务端的模型密钥无效或还没配置，请检查后端的模型配置。";

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
        {ownKeyProblem && onOpenService && (
          <button type="button" onClick={onOpenService} className="btn btn-sm mt-2.5">
            去 AI 服务换一把
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
      </div>
    </div>
  );
}
