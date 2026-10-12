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
from urllib.parse import urlsplit

from env_config import backend_dir, chroma_persist_dir
from file_service import FileService
from logging_config import get_logger
from tenancy import GLOBAL_TENANT, is_visible_to, normalize_tenant
from vector_service import VectorRAGService
from web_import import fetch_page

logger = get_logger(__name__)

# 单篇超过这个长度的文档只进向量库，不再整篇塞给 Kimi
LONG_DOC_CHARS = 20000

# 拼给 Kimi 的短文合集上限。这是一个与 LONG_DOC_CHARS 无关的独立预算：
# 短文再多，合起来超过预算就整体改走向量检索，而不是硬塞。
KIMI_CORPUS_BUDGET = 20000

SUPPORTED_EXTENSIONS = (".txt", ".pdf", ".md", ".markdown", ".docx")
_UPLOAD_EXTENSIONS = SUPPORTED_EXTENSIONS
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


def _uploads_root() -> str:
    return os.path.join(_data_dir(), "uploads")


def _uploads_dir(tenant_id: str) -> str:
    """每个租户一个子目录：两所学校上传同名文件时不会互相覆盖。"""
    return os.path.join(_uploads_root(), normalize_tenant(tenant_id))


def _safe_filename(filename: str) -> str:
    """去掉路径穿越，只保留文件名里的安全字符。"""
    name = os.path.basename(filename.replace("\\", "/")).strip() or "未命名文件"
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in _UPLOAD_EXTENSIONS:
        raise ValueError("仅支持 txt、pdf、docx 与 markdown 文件")
    stem = _UNSAFE_NAME.sub("_", stem).strip("._") or "未命名文件"
    return f"{stem}{ext}"


@dataclass
class DocumentInfo:
    name: str
    chars: int
    indexed: bool
    source: str  # "preloaded" | "uploaded" | "wiki"
    tenant_id: str = GLOBAL_TENANT


class KnowledgeBase:
    """向量库 + 短文全文的统一入口，供 API 层调用。

    所有读取都要带调用方的租户 id：预置文档与维基对所有学校可见，
    上传资料只对本校可见，与向量库的 where 过滤保持一致。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._vector_db: VectorRAGService | None = None
        self._vector_db_failed = False
        # 键是 (租户, 文档名)：不同学校可以有同名文档
        self._documents: dict[tuple[str, str], DocumentInfo] = {}
        self._short_texts: dict[tuple[str, str], str] = {}
        self._autoloaded = False

    # ---------- 向量库 ----------

    @property
    def vector_db(self) -> VectorRAGService | None:
        """首次访问时才连接 Chroma；失败后不再重试，避免每次请求都卡住。"""
        if self._vector_db is None and not self._vector_db_failed:
            try:
                self._vector_db = VectorRAGService(persist_directory=chroma_persist_dir())
            except Exception as exc:
                self._vector_db_failed = True
                logger.error(f"[Knowledge] 向量库初始化失败: {exc}")
        return self._vector_db

    # ---------- 查询 ----------

    def documents(self, tenant_id: str) -> list[DocumentInfo]:
        with self._lock:
            visible = [
                doc for (owner, _), doc in self._documents.items()
                if is_visible_to(owner, tenant_id)
            ]
        return sorted(visible, key=lambda d: d.name)

    def short_corpus(self, tenant_id: str, budget: int = KIMI_CORPUS_BUDGET) -> str:
        """拼接该租户可见的短文全文，作为 Kimi 精读路径的输入。

        超出预算直接返回空串，让调用方走向量检索。

        这里先算总长再决定是否拼接，是因为旧实现总是拼出完整字符串，
        再由 retrieve_node 拿「拼接结果 < 20000」去判路由——维基页一多就必然超标，
        于是每次请求白白拼一个十万字的串，然后原样丢弃。
        """
        with self._lock:
            items = sorted(
                (name, text)
                for (owner, name), text in self._short_texts.items()
                if is_visible_to(owner, tenant_id)
            )

        total = sum(len(text) for _, text in items)
        if total == 0 or total > budget:
            return ""

        return "".join(
            f"\n\n--- 文档来源: {name} ---\n\n{text}" for name, text in items
        )

    # ---------- 写入 ----------

    def autoload(self) -> int:
        """预载 wiki/、data/ 预置文档和各租户的上传目录。重复调用只生效一次。"""
        with self._lock:
            if self._autoloaded:
                return len(self._documents)
            self._autoloaded = True

        data_dir = _data_dir()
        uploads_root = _uploads_root()
        wiki_dir = _wiki_dir()
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(uploads_root, exist_ok=True)

        # 先载 wiki（编译后的高密度页），再载原始 PDF/上传，检索时两者并存
        self._load_wiki(wiki_dir)
        self._load_dir(data_dir, source="preloaded", tenant_id=GLOBAL_TENANT)
        self._load_uploads(uploads_root)

        logger.info(f"[Knowledge] 预载完成，共 {len(self._documents)} 个文档")
        return len(self._documents)

    def add_upload(self, filename: str, data: bytes, tenant_id: str) -> DocumentInfo:
        """把上传文件落到该租户的 uploads 子目录，解析文本后再登记进知识库。"""
        safe_name = _safe_filename(filename)
        uploads_dir = _uploads_dir(tenant_id)
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

        return self._register(safe_name, content, source="uploaded", tenant_id=tenant_id)

    def add_url(self, url: str, tenant_id: str, fetch=fetch_page) -> DocumentInfo:
        """抓取网页正文，存成该租户 uploads 下的 markdown，再走与上传相同的登记流程。

        落盘而不是只进向量库：重启后 autoload 才能重新登记，Kimi 短文路径也能读到。
        """
        page = fetch(url)
        host = urlsplit(page.url).hostname or "网页"
        stem = (page.title or host)[:60]
        markdown = f"# {page.title or host}\n\n来源: {page.url}\n\n{page.text}\n"
        return self.add_upload(f"{stem}.md", markdown.encode("utf-8"), tenant_id)

    def clear(self, tenant_id: str) -> None:
        """清空该租户上传的文档。

        预置 PDF 与维基属于全局归属，不在清空范围内——它们被删掉后
        要重启进程才会重新索引，还会白白重跑一遍 embedding。
        """
        tenant = normalize_tenant(tenant_id)
        vdb = self.vector_db
        if vdb is not None:
            vdb.clear_tenant(tenant)

        uploads_dir = _uploads_dir(tenant)
        if os.path.isdir(uploads_dir):
            for name in os.listdir(uploads_dir):
                path = os.path.join(uploads_dir, name)
                if os.path.isfile(path):
                    os.remove(path)

        with self._lock:
            for key in [k for k in self._documents if k[0] == tenant]:
                self._documents.pop(key, None)
                self._short_texts.pop(key, None)

    def _load_uploads(self, uploads_root: str) -> None:
        """uploads/ 下每个子目录是一个租户。"""
        if not os.path.isdir(uploads_root):
            return
        for entry in sorted(os.listdir(uploads_root)):
            tenant_dir = os.path.join(uploads_root, entry)
            if not os.path.isdir(tenant_dir):
                continue
            try:
                tenant = normalize_tenant(entry)
            except ValueError as exc:
                logger.warning(f"[Knowledge] 跳过非法租户目录 {entry}: {exc}")
                continue
            self._load_dir(tenant_dir, source="uploaded", tenant_id=tenant)

    def _load_dir(self, directory: str, source: str, tenant_id: str) -> None:
        paths: list[str] = []
        for ext in SUPPORTED_EXTENSIONS:
            paths.extend(glob.glob(os.path.join(directory, f"*{ext}")))
        for path in sorted(paths):
            name = os.path.basename(path)
            try:
                content = FileService.read_file(path)
            except Exception as exc:
                logger.error(f"[Knowledge] 读取失败 {name}: {exc}")
                continue
            if content.startswith("Error") or content.startswith("不支持"):
                logger.error(f"[Knowledge] 读取失败 {name}: {content[:60]}")
                continue
            self._register(name, content, source=source, tenant_id=tenant_id)

    def _load_wiki(self, wiki_dir: str) -> None:
        """递归载入 wiki 下的 markdown；跳过 schema、日志与抽取缓存。"""
        if not os.path.isdir(wiki_dir):
            logger.warning(f"[Knowledge] wiki 目录不存在，跳过: {wiki_dir}")
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
                    logger.error(f"[Knowledge] wiki 读取失败 {doc_name}: {exc}")
                    continue
                if not content or content.startswith("Error") or content.startswith("不支持"):
                    logger.error(f"[Knowledge] wiki 读取失败 {doc_name}: {(content or '')[:60]}")
                    continue
                content = _strip_frontmatter(content)
                if not content.strip():
                    continue
                self._register(doc_name, content, source="wiki", tenant_id=GLOBAL_TENANT)
                loaded += 1
        logger.info(f"[Knowledge] wiki 预载 {loaded} 页")

    # ---------- 内部 ----------

    def _register(
        self, name: str, content: str, source: str, tenant_id: str
    ) -> DocumentInfo:
        tenant = normalize_tenant(tenant_id)
        indexed = False
        vdb = self.vector_db
        if vdb is not None:
            try:
                vdb.index_document(content, name, tenant)
                indexed = True
            except Exception as exc:
                logger.error(f"[Knowledge] 建立索引失败 {name}: {exc}")

        info = DocumentInfo(
            name=name, chars=len(content), indexed=indexed, source=source, tenant_id=tenant
        )
        key = (tenant, name)
        with self._lock:
            self._documents[key] = info
            if len(content) < LONG_DOC_CHARS:
                self._short_texts[key] = content
            else:
                self._short_texts.pop(key, None)
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
