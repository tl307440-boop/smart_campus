"""知识库文档管理：进程启动时预载 data/ 目录，运行期接收上传。

从 Streamlit 的 session_state 里抽出来，让 API 进程有一份与界面无关的文档状态。
"""
from __future__ import annotations

import glob
import os
import tempfile
import threading
from dataclasses import dataclass
from typing import Optional

from env_config import backend_dir, chroma_persist_dir
from file_service import FileService
from vector_service import VectorRAGService

# 超过这个长度的文档只进向量库，不再整篇塞给 Kimi
LONG_DOC_CHARS = 20000

SUPPORTED_EXTENSIONS = (".txt", ".pdf")


@dataclass
class DocumentInfo:
    name: str
    chars: int
    indexed: bool
    source: str  # "preloaded" | "uploaded"


class KnowledgeBase:
    """向量库 + 短文全文的统一入口，供 API 层调用。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._vector_db: Optional[VectorRAGService] = None
        self._vector_db_failed = False
        self._documents: dict[str, DocumentInfo] = {}
        self._short_texts: dict[str, str] = {}
        self._autoloaded = False

    # ---------- 向量库 ----------

    @property
    def vector_db(self) -> Optional[VectorRAGService]:
        """首次访问时才连接 Chroma；失败后不再重试，避免每次请求都卡住。"""
        if self._vector_db is None and not self._vector_db_failed:
            try:
                self._vector_db = VectorRAGService(persist_directory=chroma_persist_dir())
            except Exception as exc:
                self._vector_db_failed = True
                print(f"[Knowledge] 向量库初始化失败: {exc}")
        return self._vector_db

    # ---------- 查询 ----------

    def documents(self) -> list[DocumentInfo]:
        with self._lock:
            return sorted(self._documents.values(), key=lambda d: d.name)

    def short_corpus(self) -> str:
        """拼接所有短文全文，作为 Kimi 精读路径的输入。"""
        with self._lock:
            parts = [
                f"\n\n--- 文档来源: {name} ---\n\n{text}"
                for name, text in sorted(self._short_texts.items())
            ]
        return "".join(parts)

    # ---------- 写入 ----------

    def autoload(self) -> int:
        """预载 data/ 目录下的 txt/pdf。重复调用只生效一次。"""
        with self._lock:
            if self._autoloaded:
                return len(self._documents)
            self._autoloaded = True

        data_dir = os.path.abspath(os.path.join(backend_dir(), "..", "data"))
        os.makedirs(data_dir, exist_ok=True)

        paths: list[str] = []
        for ext in SUPPORTED_EXTENSIONS:
            paths.extend(glob.glob(os.path.join(data_dir, f"*{ext}")))

        for path in paths:
            name = os.path.basename(path)
            try:
                content = FileService.read_file(path)
            except Exception as exc:
                print(f"[Knowledge] 读取失败 {name}: {exc}")
                continue
            if content.startswith("Error") or content.startswith("不支持"):
                print(f"[Knowledge] 读取失败 {name}: {content[:60]}")
                continue
            self._register(name, content, source="preloaded")

        print(f"[Knowledge] 预载完成，共 {len(self._documents)} 个文档")
        return len(self._documents)

    def add_upload(self, filename: str, data: bytes) -> DocumentInfo:
        """保存上传文件到临时路径、解析文本，再登记进知识库。"""
        ext = os.path.splitext(filename)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError("仅支持 txt 与 pdf 文件")

        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            content = FileService.read_file(tmp_path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

        if content.startswith("Error") or content.startswith("不支持"):
            raise ValueError(content)

        return self._register(filename, content, source="uploaded")

    def clear(self) -> None:
        vdb = self.vector_db
        if vdb is not None:
            vdb.clear_database()
        with self._lock:
            self._documents.clear()
            self._short_texts.clear()
            self._autoloaded = False

    # ---------- 内部 ----------

    def _register(self, name: str, content: str, source: str) -> DocumentInfo:
        indexed = False
        vdb = self.vector_db
        if vdb is not None:
            try:
                vdb.index_document(content, name)
                indexed = True
            except Exception as exc:
                print(f"[Knowledge] 建立索引失败 {name}: {exc}")

        info = DocumentInfo(name=name, chars=len(content), indexed=indexed, source=source)
        with self._lock:
            self._documents[name] = info
            if len(content) < LONG_DOC_CHARS:
                self._short_texts[name] = content
            else:
                self._short_texts.pop(name, None)
        return info


knowledge_base = KnowledgeBase()
