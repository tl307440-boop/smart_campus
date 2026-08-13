"use client";

import { useCallback, useEffect, useState } from "react";
import { type DocumentInfo, fetchDocuments } from "@/lib/api";

const LOAD_FAILED = "知识库读取失败，检查后端是否已启动";

export function useDocuments() {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

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

  return { documents, loadError, reload };
}
