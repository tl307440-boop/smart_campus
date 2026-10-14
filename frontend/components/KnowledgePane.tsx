"use client";

import { useRef, useState } from "react";
import { PaneTitle } from "@/components/ToolDrawer";
import { clearDocuments, importDocumentUrl, uploadDocument } from "@/lib/api";
import { useDocuments } from "@/hooks/useDocuments";

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message.slice(0, 160) : fallback;
}

/** wiki/concepts/国家助学金.md → ["wiki/concepts/", "国家助学金.md"] */
function splitPath(name: string): [string, string] {
  const cut = Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\")) + 1;
  return [name.slice(0, cut), name.slice(cut)];
}

/** 抽屉页「知识库」，只给老师和管理员：上传、网页导入、文档清单、清空。 */
export function KnowledgePane() {
  const { documents, loadError, reload, indexPending, indexStalled } = useDocuments();
  const [notice, setNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [url, setUrl] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const indexed = documents.filter((doc) => doc.indexed).length;

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
      setNotice(errorText(err, "导入失败"));
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
      setNotice(errorText(err, "上传失败"));
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function handleClear() {
    if (!window.confirm("清空本校知识库？所有文档的索引都会删掉，回答将查不到资料。")) return;
    setNotice(null);
    try {
      await clearDocuments();
      await reload();
      setNotice("知识库已清空");
    } catch (err) {
      setNotice(errorText(err, "清空失败"));
    }
  }

  return (
    <section>
      <PaneTitle title="知识库">
        回答会从这些资料里找依据。索引用「服务」里配置的智谱密钥，打开本页就会切片。
      </PaneTitle>

      <input
        ref={fileInput}
        type="file"
        accept=".txt,.pdf,.md,.docx"
        multiple
        onChange={(event) => void handleUpload(event.target.files)}
        className="hidden"
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button type="button" disabled={uploading} onClick={() => fileInput.current?.click()} className="btn btn-primary">
          {uploading ? "正在解析…" : "上传文档"}
        </button>
        <span className="text-sm text-muted">txt / pdf / docx / md，可多选</span>
      </div>

      <form
        className="mt-4 flex items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void handleImportUrl();
        }}
      >
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          aria-label="网页地址"
          placeholder="或粘贴网页地址导入"
          className="field flex-1"
        />
        <button type="submit" disabled={uploading || !url.trim()} className="btn">
          导入
        </button>
      </form>
      {indexPending && !indexStalled && (
        <p className="mt-3 text-sm text-muted" role="status">
          正在用智谱建立索引，完成前这一列显示未索引。
        </p>
      )}
      {indexStalled && (
        <p className="mt-3 text-sm text-muted" role="status">
          索引还没完成。到「服务」里对「知识库向量化」点测一下，多半是智谱密钥还没填。
        </p>
      )}
      {(notice ?? loadError) && (
        <p className="mt-3 text-[0.9375rem] break-all text-muted" role="status">
          {notice ?? loadError}
        </p>
      )}

      <div className="mt-6 flex items-baseline justify-between">
        <h3 className="font-display text-lg">文档</h3>
        <span className="text-sm text-muted tabular-nums">
          共 {documents.length} 篇 · 已索引 {indexed}
        </span>
      </div>
      <table className="mt-2 w-full table-fixed text-[0.9375rem]">
        <thead>
          <tr className="border-b-2 border-ink text-left text-sm text-muted">
            <th className="py-1.5 font-normal">文件</th>
            <th className="w-16 py-1.5 text-right font-normal">字数</th>
            <th className="w-20 py-1.5 text-right font-normal">索引</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => {
            const [dir, base] = splitPath(doc.name);
            return (
              <tr key={doc.name} className="border-b border-dashed border-rule">
                <td className="py-2 pr-2">
                  <p className="truncate" title={doc.name}>{base}</p>
                  {dir && <p className="truncate text-[0.8125rem] text-muted">{dir}</p>}
                </td>
                <td className="py-2 text-right tabular-nums">{doc.chars}</td>
                <td className={`py-2 text-right text-sm ${doc.indexed ? "" : "text-muted"}`}>
                  {doc.indexed ? "已索引" : "未索引"}
                </td>
              </tr>
            );
          })}
          {documents.length === 0 && (
            <tr>
              <td colSpan={3} className="py-3 text-muted">还没有文档，先上传一篇吧。</td>
            </tr>
          )}
        </tbody>
      </table>

      <div className="mt-8 border-t border-dashed border-rule pt-4">
        <button type="button" onClick={() => void handleClear()} className="btn btn-sm btn-danger">
          清空知识库
        </button>
        <p className="mt-1.5 text-sm text-muted">会删掉本校全部文档索引，无法撤销。</p>
      </div>
    </section>
  );
}
