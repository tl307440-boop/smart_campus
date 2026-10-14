import hashlib
import os
import threading
import uuid
from dataclasses import dataclass, field

import chromadb
from env_config import load_backend_env
from langchain_community.embeddings import ZhipuAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from logging_config import get_logger
from tenancy import (
    chunk_metadata,
    owner_filter,
    source_filter,
    visibility_filter,
)

logger = get_logger(__name__)


@dataclass
class RetrievedChunk:
    """检索候选片段，对应生产环境中的 chunk 对象。"""
    text: str
    metadata: dict = field(default_factory=dict)
    vector_score: float = 0.0


_cross_encoder = None
_cross_encoder_load_attempted = False


def _env_flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def get_cross_encoder():
    """
    懒加载 BGE 交叉编码器。
    未安装 sentence-transformers、模型下载失败或关闭 RERANK_ENABLED 时返回 None。
    """
    global _cross_encoder, _cross_encoder_load_attempted
    if not _env_flag("RERANK_ENABLED", "true"):
        return None
    if _cross_encoder_load_attempted:
        return _cross_encoder
    _cross_encoder_load_attempted = True
    try:
        from sentence_transformers import CrossEncoder

        model_name = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-large")
        logger.info(f"[Rerank] 正在加载交叉编码器: {model_name}")
        _cross_encoder = CrossEncoder(model_name)
        logger.info("[Rerank] 交叉编码器加载成功")
    except Exception as e:
        logger.warning(f"[Rerank] 交叉编码器不可用，将使用向量相似度截断: {e}")
        _cross_encoder = None
    return _cross_encoder


# 切片参数变更后必须让已有索引失效。内容没变时 content_hash 不变，
# 只改切片逻辑的话旧片段会一直留着，检索质量的改进等于没生效。
CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
CHUNK_SCHEME = f"recursive-v1:{CHUNK_SIZE}:{CHUNK_OVERLAP}"

_splitter_cache: dict[tuple[int, int], RecursiveCharacterTextSplitter] = {}


def _splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    key = (chunk_size, chunk_overlap)
    if key not in _splitter_cache:
        _splitter_cache[key] = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            # 默认分隔符只认英文标点，中文规章基本切不开，会一路退化成按字符硬切
            separators=["\n\n", "\n", "。", "；", "！", "？", "、", "，", " ", ""],
            keep_separator=True,
            length_function=len,
        )
    return _splitter_cache[key]


def document_content_hash(content: str) -> str:
    """文档指纹，用于判断是否需要重新向量化。

    把切片方案一起算进去：只改切分方式、内容不变时，也要触发重建。
    """
    payload = f"{CHUNK_SCHEME}\n{content}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

# // AI辅助生成：智谱AI (embedding-3) & DeepSeek-chat (Web), 2026-04
# 实现批量嵌入类以解决长文档 API 请求超限问题
class BatchedZhipuEmbeddings:
    """批量处理智谱AI嵌入，避免单次请求过多文本，上次文档过长导致API失败。
    这次我们实现了一个批量嵌入类，内部使用智谱AI的嵌入接口，但对外提供批量处理能力。
    """
    def __init__(self, model, api_key, batch_size=64):
        self.batch_size = batch_size
        self.inner = ZhipuAIEmbeddings(model=model, api_key=api_key)

    def embed_documents(self, texts):
        """Chroma 会调用这个方法"""
        all_embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i: i + self.batch_size]
            emb = self.inner.embed_documents(batch)   #调用智谱AI的批量嵌入接口，不是递归调用这个方法本身
            all_embeddings.extend(emb)
        return all_embeddings

    def embed_query(self, text):
        """检索时 Chroma 调用"""
        return self.inner.embed_query(text)


def build_embeddings(spec):
    """只接智谱。开源模型只预留了名字，这里拒绝加载，避免把 PyTorch 拉进 2G 内存的机器。"""
    if spec.provider != "zhipu":
        raise RuntimeError("开源向量模型只预留了位置，尚未接入，不会在服务器上加载")
    return BatchedZhipuEmbeddings(model=spec.model or "embedding-3", api_key=spec.api_key, batch_size=64)


_stores: dict[str, "VectorRAGService"] = {}
_store_lock = threading.Lock()


def peek_store(collection_name: str) -> "VectorRAGService | None":
    """已经加载过的向量库；没加载就返回 None，避免为了看一眼状态去下载模型。"""
    return _stores.get(collection_name)


def open_store(spec) -> "VectorRAGService | None":
    """按模型打开向量库。同一种模型进程内只加载一次。失败不缓存，下次还能再试。"""
    name = spec.collection_name
    with _store_lock:
        existing = _stores.get(name)
    if existing is not None:
        return existing
    try:
        store = VectorRAGService(
            persist_directory=_persist_dir(),
            embeddings=build_embeddings(spec),
            collection_name=name,
        )
    except Exception as exc:
        logger.error(f"[VectorDB] 打开 {spec.provider}:{spec.model} 失败: {exc}")
        return None
    with _store_lock:
        return _stores.setdefault(name, store)


def _persist_dir() -> str:
    from env_config import chroma_persist_dir

    return chroma_persist_dir()


class VectorRAGService:
    """
    方案 B：传统 RAG 引擎 (ChromaDB + 智谱 embedding-3)
    适用场景：超大文档库 (>20万字)、全校校规合集等。
    原理：切片 -> 向量化 -> 检索 Top-K 片段。
    """
    def __init__(self, persist_directory="chroma_db", embeddings=None, collection_name: str = "campus_knowledge"):
        load_backend_env()

        if embeddings is not None:
            self.embedding_model = embeddings
        else:
            # 旧的 Streamlit 入口没传模型，仍走智谱
            logger.info(" [VectorDB] 正在初始化智谱AI向量模型...")
            try:
                self.embedding_model = BatchedZhipuEmbeddings(
                    model="embedding-3",
                    api_key=os.getenv("zhi_pu_API_KEY"),
                    batch_size=64
                )
                logger.info("[VectorDB] 智谱AI向量模型加载成功！")
            except Exception as e:
                logger.error(f"[VectorDB] 连接智谱AI失败: {e}")
                raise

        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}  # 使用余弦相似度，适合文本向量检索
        )


# // AI辅助生成：DeepSeek-chat (Web), 2026-04
# 包含文本切片算法、UUID 生成及余弦相似度检索逻辑
    def split_text(
        self,
        text: str,
        chunk_size: int = CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
    ) -> list:
        """
        按语义边界递归切片：优先在段落分隔处断开，其次是中文句号、分号等，
        实在断不开才按字符硬切。

        原来是纯按字符数切，条款和表格经常被从中间截断，
        召回的片段读起来缺头少尾，重排和生成都受影响。
        """
        return _splitter(chunk_size, chunk_overlap).split_text(text)
    
    def is_file_exists(self, file_path: str, tenant_id: str) -> bool:
        """检查某租户下的这个文件是否已经建立过索引"""
        result = self.collection.get(
            where=source_filter(file_path, tenant_id),
            limit=1
        )
        return len(result['ids']) > 0

    def get_indexed_content_hash(self, file_path: str, tenant_id: str) -> str | None:
        """读取已索引文档的 content_hash；无记录则返回 None。"""
        result = self.collection.get(
            where=source_filter(file_path, tenant_id),
            limit=1,
            include=["metadatas"],
        )
        if not result["ids"]:
            return None
        meta = result["metadatas"][0] or {}
        return meta.get("content_hash")

    def index_document(self, content: str, file_path: str, tenant_id: str):
        """
        【写入】将文档切片并向量化后存入向量库。
        若同租户同 source 且 content_hash 未变，则跳过 embedding；内容变更则删除旧片段后重建。
        """
        content_hash = document_content_hash(content)
        existing_hash = self.get_indexed_content_hash(file_path, tenant_id)
        if existing_hash is not None and existing_hash == content_hash:
            logger.warning(f" [VectorDB] 文件 '{file_path}' 内容未变，跳过 API 向量化...")
            return

        if existing_hash is not None:
            logger.warning(f" [VectorDB] 文件 '{file_path}' 内容已变更，删除旧索引后重建...")
            self.collection.delete(where=source_filter(file_path, tenant_id))

        chunks = self.split_text(content)
        logger.info(f" [VectorDB] 正在将文档切分为 {len(chunks)} 个片段并建立索引...")

        ids = [str(uuid.uuid4()) for _ in range(len(chunks))]
        metadata = [chunk_metadata(file_path, content_hash, tenant_id) for _ in range(len(chunks))]

        try:
            logger.info("[VectorDB] 正在请求智谱 API (消耗 Token)...")
            embeddings = self.embedding_model.embed_documents(chunks)

            self.collection.add(
                documents=chunks,
                embeddings=embeddings,
                ids=ids,
                metadatas=metadata
            )
            logger.info(f"[VectorDB] 成功索引 {len(chunks)} 个文档片段")
        except Exception as e:
            logger.error(f" [VectorDB] 索引失败: {e}")
            raise
    
    def retrieve_candidates(self, query: str, tenant_id: str, k: int = 50) -> list[RetrievedChunk]:
        """
        第一阶段：宽召回。用智谱 embedding + Chroma 余弦检索捞一批候选 Chunk。
        当前仓库没有独立 BM25/hybrid_retriever，因此用向量宽召回承担「大网捞鱼」。

        where 过滤把候选限制在「全局文档 + 本租户文档」，跨校资料不会进入召回。
        """
        total = self.collection.count()
        if total <= 0:
            return []

        n_results = min(k, total)
        query_embedding = self.embedding_model.embed_query(query)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=visibility_filter(tenant_id),
            include=["documents", "metadatas", "distances"],
        )
        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        candidates: list[RetrievedChunk] = []
        for i, doc_text in enumerate(docs):
            if not doc_text:
                continue
            distance = float(distances[i]) if i < len(distances) else 1.0
            meta = dict(metas[i] or {}) if i < len(metas) else {}
            candidates.append(
                RetrievedChunk(
                    text=doc_text,
                    metadata=meta,
                    vector_score=1.0 - distance,
                )
            )
        logger.info(f"[VectorDB] 宽召回 {len(candidates)} 个候选片段 (请求 k={k})")
        return candidates

    def rerank_candidates(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_n: int = 3,
    ) -> list[RetrievedChunk]:
        """
        第二阶段：BGE 交叉编码器对 [Query, Document] 对打分，截断 Top-N。
        模型不可用时退回向量相似度排序。
        """
        if not candidates:
            return []

        reranker = get_cross_encoder()
        if reranker is None:
            ranked = sorted(candidates, key=lambda x: x.vector_score, reverse=True)
            logger.info(f"[Rerank] 未启用交叉编码器，按向量分截断 Top {top_n}")
            return ranked[:top_n]

        pairs = [[query, chunk.text] for chunk in candidates]
        scores = reranker.predict(pairs)
        for i, score in enumerate(scores):
            candidates[i].metadata["rerank_score"] = float(score)

        reranked = sorted(
            candidates,
            key=lambda x: x.metadata.get("rerank_score", 0.0),
            reverse=True,
        )[:top_n]
        logger.info(f"[Rerank] 交叉编码器重排完成，保留 Top {len(reranked)}")
        return reranked

    @staticmethod
    def format_context(chunks: list[RetrievedChunk]) -> str:
        """把重排后的 Chunk 拼成 generate_node 使用的上下文字符串。"""
        if not chunks:
            return ""

        retrieved_pieces = []
        source_names: list[str] = []
        for chunk in chunks:
            source_file = chunk.metadata.get("source", "未知文件")
            if source_file not in source_names:
                source_names.append(source_file)
            retrieved_pieces.append(f"{chunk.text}\n(来源: {source_file})")

        retrieved_context = "\n\n".join(retrieved_pieces)
        retrieved_context += f"\n\n 本次回答共参考了以下文件：{', '.join(source_names)}"
        return retrieved_context

    def search_chunks(
        self,
        query: str,
        tenant_id: str,
        top_k: int = 3,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]:
        """
        【读取】三阶段 RAG 检索，返回结构化片段（供引用卡片使用）：
        1. 宽召回 candidate_k 个候选（默认 50，仅限该租户可见的文档）
        2. 丢掉余弦相似度低于 min_score 的候选（工作区设置里的相似度阈值）
        3. BGE CrossEncoder 重排序后截断 Top-K

        Example:
            >>> chunks = svc.search_chunks("宿舍门禁", "t1", top_k=2, min_score=0.3)  # doctest: +SKIP
        """
        logger.info(f" [VectorDB] 正在检索与 '{query}' 最相关的片段...")
        candidate_k = _env_int("RERANK_CANDIDATE_K", 50)

        try:
            candidates = self.retrieve_candidates(query, tenant_id, k=candidate_k)
            if min_score > 0:
                candidates = [c for c in candidates if c.vector_score >= min_score]
            reranked_top = self.rerank_candidates(query, candidates, top_n=top_k)
            logger.info(f" [VectorDB] 检索到 {len(reranked_top)} 个相关片段")
            return reranked_top
        except Exception as e:
            logger.error(f" [VectorDB] 检索失败: {e}")
            return []

    def search(self, query: str, tenant_id: str, top_k: int = 3) -> str:
        """旧版 Streamlit 界面用的字符串接口。"""
        return self.format_context(self.search_chunks(query, tenant_id, top_k=top_k))
    
    def clear_tenant(self, tenant_id: str) -> None:
        """只删除某个租户写入的片段。

        不能整库删除：维基与预置文档挂在全局归属下，被清掉后要重启进程
        才会重新索引，而且会白白重跑一遍 embedding。
        """
        logger.info(f"[VectorDB] 正在清空租户 {tenant_id} 的索引...")
        self.collection.delete(where=owner_filter(tenant_id))
        logger.info(f"[VectorDB] 租户 {tenant_id} 的索引已清空")


# 使用示例
# if __name__ == "__main__":
    # 初始化服务
    # rag_service = VectorRAGService(persist_directory="chroma_db")
    
    # 示例：索引一个文档
    # sample_document = """
    # 这是一段示例文档内容。
    # 学校规定学生必须在晚上10点前返回宿舍。
    # 图书馆开放时间为早上8点到晚上10点。
    # """
    
    # rag_service.index_document(sample_document)
    
    # 示例：检索
    # result = rag_service.search("宿舍门禁时间", top_k=2)
    # print("\n检索结果:")
    # print(result)