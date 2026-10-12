"use client";

import { useRef, useState } from "react";
import { clearDocuments, importDocumentUrl, uploadDocument } from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { canManageKnowledge } from "@/lib/auth/session";
import { useDocuments } from "@/hooks/useDocuments";

interface Props {
  threadId: string | null;
  ttsEnabled: boolean;
  onToggleTts: (enabled: boolean) => void;
  onNewSession: () => void;
  onOpenSettings: () => void;
  onOpenAdmin: () => void;
}

const BUTTON =
  "w-full rounded-full border border-line bg-surface px-4 py-2 text-sm transition hover:border-line-strong hover:bg-gold-soft disabled:opacity-50";

export function ConsolePanel({
  threadId,
  ttsEnabled,
  onToggleTts,
  onNewSession,
  onOpenSettings,
  onOpenAdmin,
}: Props) {
  const { user } = useAuth();
  const { documents, loadError, reload } = useDocuments();
  const [notice, setNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [url, setUrl] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const canManage = canManageKnowledge(user?.role);

  async function handleImportUrl() {
    const target = url.trim();
    if (!target) return;
    setUploading(true);
    setNotice(null);
    try {
      const info = await importDocumentUrl(target);
      setNotice(`已导入「${info.name}」，${info.chars} 字`);
      setUrl("");
      await reload();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "导入失败");
    } finally {
      setUploading(false);
    }
  }

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
        <button
          type="button"
          onClick={onOpenSettings}
          className="w-full rounded-full border border-line bg-surface px-4 py-2 text-sm transition hover:border-line-strong hover:bg-gold-soft"
        >
          AI 服务设置
        </button>
        {user?.role === "ADMIN" && (
          <button type="button" onClick={onOpenAdmin} className={BUTTON}>
            工作区与嵌入挂件
          </button>
        )}
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
          accept=".txt,.pdf,.md,.docx"
          multiple
          onChange={(event) => handleUpload(event.target.files)}
          className="hidden"
        />
        <button
          type="button"
          disabled={uploading}
          onClick={() => fileInput.current?.click()}
          className={BUTTON}
        >
          {uploading ? "正在解析…" : "上传文档（txt / pdf / docx / md）"}
        </button>

        {canManage && (
          <div className="flex gap-2">
            <input
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="粘贴网页地址导入"
              className="min-w-0 flex-1 rounded-full border border-line bg-ink-elevated px-3 py-2 text-xs text-parchment"
            />
            <button
              type="button"
              disabled={uploading || !url.trim()}
              onClick={() => void handleImportUrl()}
              className="shrink-0 rounded-full border border-line bg-surface px-3 py-2 text-xs transition hover:border-line-strong hover:bg-gold-soft disabled:opacity-50"
            >
              导入
            </button>
          </div>
        )}

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
