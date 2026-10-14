import { Fragment, type ReactNode } from "react";

/**
 * 回答用的轻量 Markdown：标题、粗斜体、删除线、行内代码、代码块、列表（可嵌套）、引用、分隔线、表格、链接。
 * 全部走 React 文本节点，不认原始 HTML；链接只放行 http(s)。
 * tail 接在最后一段文字的末尾（流式时的铅笔、答完的小花）。
 */

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; lines: string[] }
  | { kind: "list"; ordered: boolean; start: number; items: Block[][] }
  | { kind: "quote"; blocks: Block[] }
  | { kind: "code"; text: string }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "rule" };

// 反引号围栏后面的说明里不能再有反引号：「```print(1)``` 之后…」是行内代码，不是代码块
const FENCE = /^\s*(?:(`{3,})[^`]*|(~{3,}).*)$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^\s{0,3}>\s?/;
const ITEM = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$|^(\s*)(\d{1,3}、)\s*(.*)$/;
const TABLE_DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function matchItem(line: string) {
  const m = ITEM.exec(line);
  if (!m) return null;
  const indent = (m[1] ?? m[4]).length;
  const marker = m[2] ?? m[5];
  const ordered = /\d/.test(marker);
  return { indent, ordered, start: ordered ? Number.parseInt(marker, 10) : 1, text: m[3] ?? m[6], width: indent + marker.length + 1 };
}

function isTableStart(lines: string[], i: number): boolean {
  return lines[i].includes("|") && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1]);
}

function startsBlock(lines: string[], i: number): boolean {
  const line = lines[i];
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || Boolean(matchItem(line)) || isTableStart(lines, i);
}

function splitRow(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function parseList(lines: string[], from: number): { block: Block; next: number } {
  const first = matchItem(lines[from])!;
  const items: string[][] = [];
  let i = from;
  while (i < lines.length) {
    const line = lines[i];
    const item = matchItem(line);
    if (item && item.indent <= first.indent + 1 && item.ordered === first.ordered) {
      items.push([item.text]);
      i++;
      continue;
    }
    const current = items[items.length - 1];
    if (!line.trim()) {
      const ahead = lines.slice(i + 1).findIndex((l) => l.trim());
      if (ahead < 0) break;
      const peek = lines[i + 1 + ahead];
      const peekItem = matchItem(peek);
      const indent = peek.length - peek.trimStart().length;
      if (indent > first.indent || (peekItem && peekItem.indent <= first.indent + 1 && peekItem.ordered === first.ordered)) {
        current.push("");
        i++;
        continue;
      }
      break;
    }
    const indent = line.length - line.trimStart().length;
    if (indent > first.indent) {
      current.push(line.slice(Math.min(indent, first.width)));
    } else if (!item && !startsBlock(lines, i) && current[current.length - 1]?.trim()) {
      current.push(line.trim());
    } else {
      break;
    }
    i++;
  }
  return { block: { kind: "list", ordered: first.ordered, start: first.start, items: items.map(parseBlocks) }, next: i };
}

function parseBlocks(lines: string[]): Block[] {
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1] ?? fence[2];
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith(marker)) body.push(lines[i++]);
      i++;
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }
    if (RULE.test(line)) {
      blocks.push({ kind: "rule" });
      i++;
      continue;
    }
    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) body.push(lines[i++].replace(QUOTE, ""));
      blocks.push({ kind: "quote", blocks: parseBlocks(body) });
      continue;
    }
    if (isTableStart(lines, i)) {
      const head = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(splitRow(lines[i++]));
      blocks.push({ kind: "table", head, rows });
      continue;
    }
    if (matchItem(line)) {
      const { block, next } = parseList(lines, i);
      blocks.push(block);
      i = next;
      continue;
    }
    const body = [line.trim()];
    i++;
    while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) body.push(lines[i++].trim());
    blocks.push({ kind: "paragraph", lines: body });
  }
  return blocks;
}

// 粗体只认 **：下划线在 snake__case 这类词中间太常见
const INLINE =
  /(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)|\*\*(?=\S)([\s\S]*?\S)\*\*|~~(?=\S)([\s\S]*?\S)~~|\*(?=[^\s*])([^*]*?[^\s*])\*|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>()（）\u4e00-\u9fff]*[^\s<>()（）\u4e00-\u9fff.,;:!?。，；：！？、])/g;

const LINK = "break-all text-ink underline decoration-pink decoration-2 underline-offset-2";

/** links=false：已经在链接里了，里面的网址按文字显示，免得 <a> 套 <a> */
function inline(text: string, key: string, links = true): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}.${n++}`;
    if (m[2] !== undefined) {
      out.push(<code key={k} className="rounded-md bg-skin px-1 py-0.5 font-mono text-[0.875em]">{m[2]}</code>);
    } else if (m[3] !== undefined) {
      out.push(<strong key={k} className="font-semibold">{inline(m[3], k, links)}</strong>);
    } else if (m[4] !== undefined) {
      out.push(<del key={k}>{inline(m[4], k, links)}</del>);
    } else if (m[5] !== undefined) {
      out.push(<em key={k}>{inline(m[5], k, links)}</em>);
    } else if (!links) {
      out.push(m[6] ?? m[0]);
    } else {
      const href = m[7] ?? m[8];
      out.push(
        <a key={k} href={href} target="_blank" rel="noreferrer" className={LINK}>
          {m[6] !== undefined ? inline(m[6], k, false) : href}
        </a>,
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function lines(parts: string[], key: string): ReactNode[] {
  return parts.flatMap((line, i) => (i ? [<br key={`${key}.br${i}`} />, ...inline(line, `${key}.${i}`)] : inline(line, `${key}.${i}`)));
}

const HEADING_CLASS = ["", "text-xl", "text-xl", "text-lg", "text-base", "text-base", "text-base"];

function renderBlock(block: Block, key: string, tail: ReactNode, tight = false): ReactNode {
  switch (block.kind) {
    case "heading": {
      const Tag = `h${Math.min(block.level + 1, 6)}` as "h2";
      return (
        <Tag key={key} className={`font-display leading-snug ${HEADING_CLASS[block.level]} mt-1`}>
          {inline(block.text, key)}
          {tail}
        </Tag>
      );
    }
    case "paragraph":
      return tight ? (
        <Fragment key={key}>
          {lines(block.lines, key)}
          {tail}
        </Fragment>
      ) : (
        <p key={key}>
          {lines(block.lines, key)}
          {tail}
        </p>
      );
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag
          key={key}
          start={block.ordered && block.start !== 1 ? block.start : undefined}
          className={`space-y-1 pl-6 marker:text-pink ${block.ordered ? "list-decimal marker:font-semibold" : "list-disc"}`}
        >
          {block.items.map((item, i) => (
            <li key={`${key}.${i}`} className="pl-0.5">
              {renderBlocks(item, `${key}.${i}`, i === block.items.length - 1 ? tail : null, item.length === 1)}
            </li>
          ))}
        </Tag>
      );
    }
    case "quote":
      return (
        <blockquote key={key} className="space-y-2 border-l-4 border-marker pl-3 text-muted">
          {renderBlocks(block.blocks, key, tail)}
        </blockquote>
      );
    case "code":
      return (
        <Fragment key={key}>
          <pre className="sketch overflow-x-auto bg-paper px-3 py-2.5 font-mono text-sm leading-relaxed">
            <code>{block.text}</code>
          </pre>
          {tail}
        </Fragment>
      );
    case "table":
      return (
        <Fragment key={key}>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[0.9375rem]">
              <thead>
                <tr>
                  {block.head.map((cell, i) => (
                    <th key={i} className="border-b-2 border-ink px-2 py-1.5 text-left font-semibold">{inline(cell, `${key}.h${i}`)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, r) => (
                  <tr key={r} className="border-b border-rule">
                    {block.head.map((_, c) => (
                      <td key={c} className="px-2 py-1.5 align-top">{inline(row[c] ?? "", `${key}.${r}.${c}`)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {tail}
        </Fragment>
      );
    case "rule":
      return (
        <Fragment key={key}>
          <hr className="border-t-2 border-dashed border-rule" />
          {tail}
        </Fragment>
      );
  }
}

function renderBlocks(blocks: Block[], key: string, tail: ReactNode, tight = false): ReactNode {
  if (!blocks.length) return tail;
  return blocks.map((block, i) => renderBlock(block, `${key}.${i}`, i === blocks.length - 1 ? tail : null, tight));
}

export function Markdown({ text, tail = null }: { text: string; tail?: ReactNode }) {
  const blocks = parseBlocks(text.replace(/\r\n?/g, "\n").split("\n"));
  return <div className="space-y-3 leading-[1.75] break-words">{renderBlocks(blocks, "md", tail)}</div>;
}
