"""知识库文档管理：进程启动时预载 data/、wiki/ 与 data/uploads/，运行期接收上传。

从 Streamlit 的 session_state 里抽出来，让 API 进程有一份与界面无关的文档状态。
上传文件会落盘到 data/uploads/（Dify / FastGPT 同类做法），重启后列表和 Kimi 短文路径才能对上 Chroma。

wiki/ 是 LLM Wiki 编译层（见 wiki/AGENTS.md）：优先索引结构化 markdown，
排除 schema/日志/抽取缓存，避免噪声进入检索。
"""
from __future__ import annotations

import glob
import os
import re
import threading
from dataclasses import dataclass
from typing import Optional

from env_config import backend_dir, chroma_persist_dir
from file_service import FileService
from vector_service import VectorRAGService

# 超过这个长度的文档只进向量库，不再整篇塞给 Kimi
LONG_DOC_CHARS = 20000

SUPPORTED_EXTENSIONS = (".txt", ".pdf", ".md", ".markdown")
_UPLOAD_EXTENSIONS = (".txt", ".pdf", ".md", ".markdown")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")

# wiki 内不进 RAG 的文件名（schema / 日志 / 纯导航可按需调整）
_WIKI_SKIP_NAMES = {
    "agents.md",
    "log.md",
}
_WIKI_SKIP_DIR_NAMES = {
    "_raw_extract",
}


def _data_dir() -> str:
    return os.path.abspath(os.path.join(backend_dir(), "..", "data"))


def _wiki_dir() -> str:
    return os.path.abspath(os.path.join(backend_dir(), "..", "wiki"))


def _uploads_dir() -> str:
    return os.path.join(_data_dir(), "uploads")


def _safe_filename(filename: str) -> str:
    """去掉路径穿越，只保留文件名里的安全字符。"""
    name = os.path.basename(filename.replace("\\", "/")).strip() or "未命名文件"
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in _UPLOAD_EXTENSIONS:
        raise ValueError("仅支持 txt、pdf 与 markdown 文件")
    stem = _UNSAFE_NAME.sub("_", stem).strip("._") or "未命名文件"
    return f"{stem}{ext}"


@dataclass
class DocumentInfo:
    name: str
    chars: int
    indexed: bool
    source: str  # "preloaded" | "uploaded" | "wiki"


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
        """预载 wiki/、data/ 预置文档和 data/uploads/。重复调用只生效一次。"""
        with self._lock:
            if self._autoloaded:
                return len(self._documents)
            self._autoloaded = True

        data_dir = _data_dir()
        uploads_dir = _uploads_dir()
        wiki_dir = _wiki_dir()
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(uploads_dir, exist_ok=True)

        # 先载 wiki（编译后的高密度页），再载原始 PDF/上传，检索时两者并存
        self._load_wiki(wiki_dir)
        self._load_dir(data_dir, source="preloaded")
        self._load_dir(uploads_dir, source="uploaded")

        print(f"[Knowledge] 预载完成，共 {len(self._documents)} 个文档")
        return len(self._documents)

    def add_upload(self, filename: str, data: bytes) -> DocumentInfo:
        """把上传文件落到 data/uploads/，解析文本后再登记进知识库。"""
        safe_name = _safe_filename(filename)
        uploads_dir = _uploads_dir()
        os.makedirs(uploads_dir, exist_ok=True)
        dest = os.path.join(uploads_dir, safe_name)
        with open(dest, "wb") as handle:
            handle.write(data)

        try:
            content = FileService.read_file(dest)
        except Exception:
            os.remove(dest)
            raise

        if content.startswith("Error") or content.startswith("不支持"):
            os.remove(dest)
            raise ValueError(content)

        return self._register(safe_name, content, source="uploaded")

    def clear(self) -> None:
        vdb = self.vector_db
        if vdb is not None:
            vdb.clear_database()
        uploads_dir = _uploads_dir()
        if os.path.isdir(uploads_dir):
            for name in os.listdir(uploads_dir):
                path = os.path.join(uploads_dir, name)
                if os.path.isfile(path):
                    os.remove(path)
        with self._lock:
            self._documents.clear()
            self._short_texts.clear()
            self._autoloaded = False

    def _load_dir(self, directory: str, source: str) -> None:
        paths: list[str] = []
        for ext in SUPPORTED_EXTENSIONS:
            paths.extend(glob.glob(os.path.join(directory, f"*{ext}")))
        for path in sorted(paths):
            name = os.path.basename(path)
            try:
                content = FileService.read_file(path)
            except Exception as exc:
                print(f"[Knowledge] 读取失败 {name}: {exc}")
                continue
            if content.startswith("Error") or content.startswith("不支持"):
                print(f"[Knowledge] 读取失败 {name}: {content[:60]}")
                continue
            self._register(name, content, source=source)

    def _load_wiki(self, wiki_dir: str) -> None:
        """递归载入 wiki 下的 markdown；跳过 schema、日志与抽取缓存。"""
        if not os.path.isdir(wiki_dir):
            print(f"[Knowledge] wiki 目录不存在，跳过: {wiki_dir}")
            return

        loaded = 0
        for root, dirnames, filenames in os.walk(wiki_dir):
            dirnames[:] = [
                d for d in dirnames if d not in _WIKI_SKIP_DIR_NAMES and not d.startswith(".")
            ]
            for filename in sorted(filenames):
                ext = os.path.splitext(filename)[1].lower()
                if ext not in {".md", ".markdown"}:
                    continue
                if filename.lower() in _WIKI_SKIP_NAMES:
                    continue
                path = os.path.join(root, filename)
                rel = os.path.relpath(path, wiki_dir).replace("\\", "/")
                doc_name = f"wiki/{rel}"
                try:
                    content = FileService.read_file(path)
                except Exception as exc:
                    print(f"[Knowledge] wiki 读取失败 {doc_name}: {exc}")
                    continue
                if not content or content.startswith("Error") or content.startswith("不支持"):
                    print(f"[Knowledge] wiki 读取失败 {doc_name}: {(content or '')[:60]}")
                    continue
                content = _strip_frontmatter(content)
                if not content.strip():
                    continue
                self._register(doc_name, content, source="wiki")
                loaded += 1
        print(f"[Knowledge] wiki 预载 {loaded} 页")

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


def _strip_frontmatter(text: str) -> str:
    """去掉开头的 YAML frontmatter（--- ... ---）。"""
    if not text.startswith("---"):
        return text
    match = re.match(r"^---\r?\n.*?\r?\n---\r?\n?", text, flags=re.DOTALL)
    if not match:
        return text
    return text[match.end() :].lstrip("\r\n")


knowledge_base = KnowledgeBase()
