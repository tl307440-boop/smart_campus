const QUESTIONS = [
  "国家助学金怎么申请？",
  "补考一般什么时候考？",
  "休学要走哪些流程？",
  "宿舍报修找谁？",
] as const;

interface Props {
  disabled: boolean;
  onAsk: (question: string) => void;
  /** 挂件里地方小，只放前几条 */
  limit?: number;
}

/** 空白页上的常见问题，点一下就直接发出去。 */
export function SuggestedQuestions({ disabled, onAsk, limit = QUESTIONS.length }: Props) {
  return (
    <div>
      <p className="text-sm text-muted">可以先问问这些</p>
      <ul className="mt-2 space-y-1.5">
        {QUESTIONS.slice(0, limit).map((question) => (
          <li key={question}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onAsk(question)}
              className="link-ink py-0.5 text-left font-display text-lg leading-snug disabled:cursor-wait disabled:opacity-50"
            >
              {question}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
