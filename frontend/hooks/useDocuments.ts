"use client";

import { useCallback, useEffect, useState } from "react";
import { type DocumentInfo, fetchDocuments } from "@/lib/api";

const LOAD_FAILED = "知识库读取失败，检查后端是否已启动";

export function useDocuments() {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [indexStalled, setIndexStalled] = useState(false);
  const indexPending = documents.length > 0 && documents.some((doc) => !doc.indexed);

  const reload = useCallback(async () => {
    const docs = await fetchDocuments().catch(() => null);
    if (docs) {
      setDocuments(docs);
      setLoadError(null);
    } else {
      setLoadError(LOAD_FAILED);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const docs = await fetchDocuments().catch(() => null);
      if (cancelled) return;
      if (docs) setDocuments(docs);
      else setLoadError(LOAD_FAILED);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 索引都建好了就把「卡住」清掉；放在渲染里而不是 effect 里，免得多渲染一轮
  if (!indexPending && indexStalled) setIndexStalled(false);

  useEffect(() => {
    if (!indexPending) return;
    const timer = window.setTimeout(() => setIndexStalled(true), 120_000);
    return () => window.clearTimeout(timer);
  }, [indexPending]);

  useEffect(() => {
    if (!indexPending || indexStalled) return;
    const timer = window.setInterval(() => {
      void reload();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [indexPending, indexStalled, reload]);

  return { documents, loadError, reload, indexPending, indexStalled };
}
