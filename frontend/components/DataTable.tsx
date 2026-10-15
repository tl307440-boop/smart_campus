"use client";

import { useMemo, useState } from "react";
import { useCopy } from "@/hooks/useCopy";
import { CheckIcon, CopyIcon } from "@/components/SketchIcons";
import type { ChatTable } from "@/lib/api";

/** 收起时显示几行；空教室一查就是上百间，全摊开会把回答挤到看不见 */
const COLLAPSED_ROWS = 8;
/** 超过这么多行才给筛选框 */
const FILTER_FROM_ROWS = 12;

/** 制表符分隔：粘到 Excel / WPS 里正好一格一格 */
function toTsv(table: ChatTable): string {
  const clean = (cell: string) => cell.replace(/[\t\n]+/g, " ");
  return [
    table.columns.map((column) => clean(column.label)).join("\t"),
    ...table.rows.map((row) => table.columns.map((column) => clean(row[column.key] ?? "")).join("\t")),
  ].join("\n");
}

/** 工具查出来的完整结果：后端直接给结构化数据，不经过模型转述，一行都不会丢或编。 */
export function DataTable({ table }: { table: ChatTable }) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState("");
  const { copied, copy } = useCopy();

  const rows = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return table.rows;
    return table.rows.filter((row) => {
      const text = table.columns.map((column) => row[column.key] ?? "").join(" ").toLowerCase();
      return words.every((word) => text.includes(word));
    });
  }, [table, query]);

  const shown = expanded || query ? rows : rows.slice(0, COLLAPSED_ROWS);
  const hidden = rows.length - shown.length;
  const clipped = table.total > table.rows.length;

  return (
    <section className="sketch mt-4 bg-paper" aria-label={table.title}>
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-dashed border-rule px-3 py-2">
        <p className="font-display text-base leading-tight">{table.title}</p>
        <p className="text-[0.8125rem] text-muted tabular-nums">
          共 {table.total} 条{clipped ? `，这里列出前 ${table.rows.length} 条` : ""}
        </p>
        <span className="flex-1" />
        {table.rows.length > FILTER_FROM_ROWS && (
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="筛一下，如 三教"
            aria-label={`在${table.title}里筛选`}
            className="field w-32 py-0.5 text-sm"
          />
        )}
        <button type="button" onClick={() => copy(toTsv(table))} className="link-ink text-sm" aria-label={`复制${table.title}表格`}>
          {copied ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
          {copied ? "已复制" : "复制表格"}
        </button>
      </header>

      <div className="max-h-[28rem] overflow-auto">
        <table className="w-full border-collapse text-[0.9375rem]">
          <thead className="sticky top-0 bg-paper">
            <tr>
              {table.columns.map((column) => (
                <th key={column.key} scope="col" className="border-b-2 border-ink px-3 py-1.5 text-left font-semibold whitespace-nowrap">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, r) => (
              <tr key={r} className="border-b border-rule last:border-b-0">
                {table.columns.map((column) => (
                  <td key={column.key} className="px-3 py-1.5 align-top">
                    {row[column.key]}
                  </td>
                ))}
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={table.columns.length} className="px-3 py-3 text-center text-muted">
                  没有符合「{query}」的
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {(hidden > 0 || (expanded && rows.length > COLLAPSED_ROWS && !query)) && (
        <div className="border-t border-dashed border-rule px-3 py-1.5">
          <button type="button" onClick={() => setExpanded((v) => !v)} className="link-ink text-sm" aria-expanded={expanded}>
            {expanded ? "收起" : `展开其余 ${hidden} 条`}
          </button>
        </div>
      )}
    </section>
  );
}
