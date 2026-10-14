import type { SafetyCardData } from "@/lib/api";

/** "110 / 120" → ["110", "120"] */
function phoneNumbers(phone: string): string[] {
  return phone.split("/").map((part) => part.trim()).filter(Boolean);
}

/**
 * 同学流露出伤害自己的念头时附在回答下面的求助卡片。内容由后端 comfort.safety_card 给出，
 * 不经过模型；刷新后从会话历史恢复。
 */
export function SafetyCard({ card }: { card: SafetyCardData }) {
  return (
    <aside aria-label="求助方式" className="sketch mt-4 bg-skin px-4 py-3.5">
      <p className="font-display text-lg leading-snug">你不用一个人扛着</p>
      <p className="mt-0.5 text-[0.9375rem] text-muted">现在就可以找人说说，电话那头是愿意听你说话的人。</p>
      <ul className="mt-3 space-y-2">
        {card.hotlines.map((line) => (
          <li key={line.phone} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
            <span>{line.name}</span>
            <span className="flex gap-2">
              {phoneNumbers(line.phone).map((number) => (
                <a key={number} href={`tel:${number}`} className="btn btn-sm font-display text-lg tabular-nums">
                  {number}
                </a>
              ))}
            </span>
          </li>
        ))}
      </ul>
      {card.school && (
        <p className="mt-3 border-t border-dashed border-rule pt-2.5 text-[0.9375rem] break-words">
          <span className="text-muted">学校心理咨询：</span>
          {card.school}
        </p>
      )}
    </aside>
  );
}