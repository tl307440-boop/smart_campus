"use client";

import { useRef, useState } from "react";
import { clearDocuments, uploadDocument } from "@/lib/api";
import { useDocuments } from "@/hooks/useDocuments";

interface Props {
  threadId: string | null;
  ttsEnabled: boolean;
  onToggleTts: (enabled: boolean) => void;
  onNewSession: () => void;
}

export function ConsolePanel({ threadId, ttsEnabled, onToggleTts, onNewSession }: Props) {
  const { documents, loadError, reload } = useDocuments();
  const [notice, setNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function handleUpload(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    setNotice(null);
    try {
      for (const file of Array.from(files)) {
        const info = await uploadDocument(file);
        setNotice(`已加载「${info.name}」，${info.chars} 字`);
      }
      await reload();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function handleClear() {
    setNotice(null);
    try {
      await clearDocuments();
      await reload();
      setNotice("向量库已清空");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "清空失败");
    }
  }

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col gap-5 overflow-y-auto border-r border-line bg-linear-to-b from-[#101820] to-[#0c1319] p-5">
      <div>
        <h2 className="font-display text-lg text-parchment">小懒控制台</h2>
        <p className="mt-1 text-xs text-muted">会话与知识库设置</p>
      </div>

      <section className="space-y-2">
        <p className="text-[0.7rem] tracking-[0.14em] text-muted uppercase">会话</p>
        <p className="font-mono text-xs break-all text-muted">
          {threadId ? `${threadId.slice(0, 8)}…` : "连接中…"}
        </p>
        <p className="text-xs text-muted">地址栏保留 tid，刷新后仍是同一段对话。</p>
        <button
          type="button"
          onClick={onNewSession}
          className="w-full rounded-full border border-line bg-surface px-4 py-2 text-sm transition hover:border-line-strong hover:bg-gold-soft"
        >
          开始新会话
        </button>
      </section>

      <section className="space-y-2">
        <p className="text-[0.7rem] tracking-[0.14em] text-muted uppercase">交互</p>
        <label className="flex cursor-pointer items-center justify-between rounded-xl border border-line bg-surface px-3 py-2 text-sm">
          <span>语音播报</span>
          <input
            type="checkbox"
            checked={ttsEnabled}
            onChange={(event) => onToggleTts(event.target.checked)}
            className="size-4 accent-gold"
          />
        </label>
        <p className="text-xs text-muted">使用浏览器本地语音，不额外调用接口。</p>
      </section>

      <section className="space-y-2">
        <p className="text-[0.7rem] tracking-[0.14em] text-muted uppercase">知识库</p>

        <input
          ref={fileInput}
          type="file"
          accept=".txt,.pdf"
          multiple
          onChange={(event) => handleUpload(event.target.files)}
          className="hidden"
        />
        <button
          type="button"
          disabled={uploading}
          onClick={() => fileInput.current?.click()}
          className="w-full rounded-full border border-line bg-surface px-4 py-2 text-sm transition hover:border-line-strong hover:bg-gold-soft disabled:opacity-50"
        >
          {uploading ? "正在解析…" : "上传文档（txt / pdf）"}
        </button>

        <ul className="space-y-1">
          {documents.map((doc) => (
            <li
              key={doc.name}
              className="rounded-lg border border-line bg-ink-elevated px-3 py-2 text-xs"
            >
              <p className="truncate text-parchment" title={doc.name}>
                {doc.name}
              </p>
              <p className="text-muted">
                {doc.chars} 字 · {doc.indexed ? "已索引" : "未索引"}
              </p>
            </li>
          ))}
          {documents.length === 0 && (
            <li className="text-xs text-muted">暂无文档，可先上传或放入 data/ 目录。</li>
          )}
        </ul>

        <button
          type="button"
          onClick={handleClear}
          className="w-full rounded-full border border-line px-4 py-2 text-sm text-danger transition hover:border-danger"
        >
          清空向量库
        </button>
      </section>

      {(notice ?? loadError) && (
        <p className="rounded-xl border border-line bg-surface px-3 py-2 text-xs text-muted">
          {notice ?? loadError}
        </p>
      )}
    </aside>
  );
}
