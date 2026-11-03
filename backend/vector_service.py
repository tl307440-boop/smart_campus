import hashlib
import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

import chromadb
import numpy as np
from env_config import load_backend_env
from hybrid_search import BM25, ZhipuRerankClient, rrf_fuse
from langchain_community.embeddings import ZhipuAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from logging_config import get_logger
from tenancy import (
    chunk_metadata,
    normalize_tenant,
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
    # Chroma 里的片段 id，混合检索靠它给「向量」和「BM25」两路结果去重
    chunk_id: str = ""


_cross_encoder = None
_cross_encoder_load_attempted = False


def _env_flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


# 题级门控的平台默认阈值。依据 eval_rag/results/report.md 第 5 节：
# 0.45 时有答案的题 0% 被误拒，同时挡掉 75%~83% 的库外问题；0.5 开始误拒有答案的题。
DEFAULT_MIN_SCORE = 0.45

RERANK_PROVIDERS = ("none", "zhipu", "local")
RERANK_MAX_DOCUMENTS = ZhipuRerankClient.max_documents
# 云端重排连续失败后暂停多久。不暂停的话，限流或断网时每个问题都要白等一个超时。
RERANK_COOLDOWN_SECONDS = 30.0
_rerank_cooldown_until = 0.0
_warned_providers: set[str] = set()


def default_min_score() -> float:
    """平台默认的题级门控阈值，可用 RAG_MIN_SCORE_DEFAULT 调整；设 0 表示默认不拒答。"""
    return _env_float("RAG_MIN_SCORE_DEFAULT", DEFAULT_MIN_SCORE, 0.0, 1.0)


def hybrid_enabled() -> bool:
    """BM25 + 向量混合检索开关。出问题时设 RETRIEVAL_HYBRID=false 可退回纯向量。"""
    return _env_flag("RETRIEVAL_HYBRID", "true")


def rerank_provider() -> str:
    """重排用谁：none（不重排）| zhipu（智谱云端）| local（本机 BGE 交叉编码器，需要 PyTorch）。

    RERANK_PROVIDER 优先；没设时沿用旧开关：RERANK_ENABLED 为真走 local，为假走 none。
    """
    raw = os.getenv("RERANK_PROVIDER", "").strip().lower()
    if raw in RERANK_PROVIDERS:
        return raw
    if raw and raw not in _warned_providers:
        _warned_providers.add(raw)
        logger.warning(f"[Rerank] 不认识的 RERANK_PROVIDER={raw!r}，改用 RERANK_ENABLED 判断")
    return "local" if _env_flag("RERANK_ENABLED", "true") else "none"


def get_cross_encoder():
    """
    懒加载 BGE 交叉编码器。
    未安装 sentence-transformers、模型下载失败或重排方式不是 local 时返回 None。
    """
    global _cross_encoder, _cross_encoder_load_attempted
    if rerank_provider() != "local":
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


def _rerank_api_key(tenant_id: str | None) -> str:
    """云端重排用的智谱密钥：学校在「AI 服务」页配的向量化密钥 > 服务器 .env 的 zhi_pu_API_KEY。"""
    try:
        from ai_config import embedding_spec

        if tenant_id:
            spec = embedding_spec(tenant_id)
            if spec is not None and spec.api_key:
                return spec.api_key
    except Exception as exc:
        logger.warning(f"[Rerank] 读取学校密钥失败，改用服务器密钥: {exc}")
    value = os.getenv("zhi_pu_API_KEY", "").strip()
    return "" if value.startswith("your_") else value


# BM25 内存索引：每个租户一份，只含该租户可见的片段。
# 上限防止租户很多时内存涨个不停；被挤掉的下次检索会重建（几百个片段，毫秒级）。
_BM25_MAX_TENANTS = 32
_bm25_init_lock = threading.Lock()


@dataclass
class _Bm25Index:
    ids: list[str]
    texts: list[str]
    metadatas: list[dict]
    bm25: BM25


@dataclass
class _Bm25State:
    version: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    # 租户 -> ((版本, 片段总数), 索引)
    indexes: "OrderedDict[str, tuple[tuple[int, int], _Bm25Index]]" = field(default_factory=OrderedDict)


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
            self._bump_corpus_version()

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
            self._bump_corpus_version()
            logger.info(f"[VectorDB] 成功索引 {len(chunks)} 个文档片段")
        except Exception as e:
            logger.error(f" [VectorDB] 索引失败: {e}")
            raise
    
    # ---------- BM25 内存索引 ----------

    def _bm25_state(self) -> "_Bm25State":
        state = self.__dict__.get("_bm25")
        if state is None:
            with _bm25_init_lock:
                state = self.__dict__.setdefault("_bm25", _Bm25State())
        return state

    def _bump_corpus_version(self) -> None:
        """知识库内容变了（上传、重建、清空）：让所有租户的 BM25 索引在下次检索时重建。"""
        state = self._bm25_state()
        with state.lock:
            state.version += 1
            state.indexes.clear()

    def _bm25_for(self, tenant_id: str) -> "_Bm25Index":
        """某租户可见文档的 BM25 索引，懒加载。

        语料只取「全局文档 + 本租户文档」（和向量检索用同一个 where 过滤），
        分词统计和 IDF 也只在这批文档里算，A 校的私有资料不会进 B 校的索引。
        缓存按租户分开放；版本号或片段总数一变（含别的进程写入）就重建。
        """
        tenant = normalize_tenant(tenant_id)
        state = self._bm25_state()
        stamp = (state.version, self.collection.count())
        with state.lock:
            hit = state.indexes.get(tenant)
            if hit is not None and hit[0] == stamp:
                state.indexes.move_to_end(tenant)
                return hit[1]

        rows = self.collection.get(where=visibility_filter(tenant), include=["documents", "metadatas"])
        ids = rows.get("ids") or []
        documents = rows.get("documents") or []
        metadatas = rows.get("metadatas") or []
        # Chroma 返回的顺序不保证稳定，按 (文档, 文本) 排好，BM25 并列分数时的取舍才可复现
        items = sorted(
            (
                (id_, text, dict(metadatas[i] or {}) if i < len(metadatas) else {})
                for i, (id_, text) in enumerate(zip(ids, documents, strict=False))
                if text
            ),
            key=lambda item: (item[2].get("source", ""), item[1]),
        )
        index = _Bm25Index(
            ids=[item[0] for item in items],
            texts=[item[1] for item in items],
            metadatas=[item[2] for item in items],
            bm25=BM25([item[1] for item in items]),
        )
        with state.lock:
            state.indexes[tenant] = (stamp, index)
            state.indexes.move_to_end(tenant)
            while len(state.indexes) > _BM25_MAX_TENANTS:
                state.indexes.popitem(last=False)
        logger.info(f"[BM25] 租户 {tenant} 的关键词索引已建立，共 {len(index.texts)} 个片段")
        return index

    def _fuse_with_bm25(
        self, query: str, tenant_id: str, vector_candidates: list[RetrievedChunk], k: int
    ) -> list[RetrievedChunk]:
        """向量 Top-k 与 BM25 Top-k 做 RRF 融合，返回融合后的前 k 个。

        BM25 独自捞回来的片段没有向量分，这里先标 0.0，由 search_chunks 在最后补算真实余弦。
        """
        index = self._bm25_for(tenant_id)
        by_key: dict[object, RetrievedChunk] = {}
        vector_ranking: list[object] = []
        for chunk in vector_candidates:
            key = chunk.chunk_id or (chunk.metadata.get("source", ""), chunk.text)
            by_key.setdefault(key, chunk)
            vector_ranking.append(key)

        bm25_ranking: list[object] = []
        for position, _score in index.bm25.top(query, k):
            chunk_id = index.ids[position]
            meta = index.metadatas[position]
            key = chunk_id or (meta.get("source", ""), index.texts[position])
            by_key.setdefault(
                key, RetrievedChunk(text=index.texts[position], metadata=dict(meta), vector_score=0.0, chunk_id=chunk_id)
            )
            bm25_ranking.append(key)

        fused = rrf_fuse([vector_ranking, bm25_ranking])[:k]
        return [by_key[key] for key, _ in fused]

    def _fill_vector_scores(
        self, chunks: list[RetrievedChunk], query_embedding: list[float], known_ids: set[str]
    ) -> None:
        """给 BM25 独自捞回来的片段补上真实的余弦相似度，引用卡片上才不会显示 0 分。"""
        missing = [c for c in chunks if c.chunk_id and c.chunk_id not in known_ids]
        if not missing:
            return
        try:
            stored = self.collection.get(ids=[c.chunk_id for c in missing], include=["embeddings"])
            stored_embeddings = stored.get("embeddings")
            vectors = dict(zip(
                stored.get("ids") or [],
                stored_embeddings if stored_embeddings is not None else [],
                strict=False,
            ))
            query_vec = np.asarray(query_embedding, dtype=np.float32)
            query_norm = float(np.linalg.norm(query_vec))
            for chunk in missing:
                vec = vectors.get(chunk.chunk_id)
                if vec is None or query_norm == 0.0:
                    continue
                vec = np.asarray(vec, dtype=np.float32)
                norm = float(np.linalg.norm(vec))
                if norm > 0.0:
                    chunk.vector_score = float(vec @ query_vec) / (norm * query_norm)
        except Exception as exc:
            logger.warning(f"[VectorDB] 补算向量相似度失败，引用分数按 0 显示: {exc}")

    # ---------- 召回与重排 ----------

    def _vector_candidates(
        self, query: str, tenant_id: str, k: int
    ) -> tuple[list[RetrievedChunk], list[float]]:
        """向量宽召回，同时把查询向量带出来（题级门控之后补算向量分还要用）。"""
        total = self.collection.count()
        if total <= 0:
            return [], []

        n_results = min(k, total)
        query_embedding = self.embedding_model.embed_query(query)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=visibility_filter(tenant_id),
            include=["documents", "metadatas", "distances"],
        )
        ids = (results.get("ids") or [[]])[0]
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
                    chunk_id=str(ids[i]) if i < len(ids) else "",
                )
            )
        logger.info(f"[VectorDB] 宽召回 {len(candidates)} 个候选片段 (请求 k={k})")
        return candidates, list(query_embedding)

    def retrieve_candidates(self, query: str, tenant_id: str, k: int = 50) -> list[RetrievedChunk]:
        """
        第一阶段：向量宽召回。用智谱 embedding + Chroma 余弦检索捞一批候选 Chunk。

        where 过滤把候选限制在「全局文档 + 本租户文档」，跨校资料不会进入召回。
        BM25 的那一路在 search_chunks 里融合。
        """
        return self._vector_candidates(query, tenant_id, k)[0]

    def rerank_candidates(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_n: int = 3,
        tenant_id: str | None = None,
        keep_order: bool = False,
    ) -> list[RetrievedChunk]:
        """
        最后一阶段：按 RERANK_PROVIDER 重排并截断 Top-N。

        - zhipu：智谱云端 rerank。超时、限流或任何异常都降级，绝不让问答卡住。
        - local：本机 BGE 交叉编码器（需要 PyTorch；不可用时降级）。
        - none：不重排。

        降级时 keep_order=True 保留传进来的顺序（混合检索的 RRF 排序），
        否则按向量相似度截断（旧行为）。
        """
        if not candidates:
            return []

        def degrade(reason: str) -> list[RetrievedChunk]:
            if keep_order:
                logger.info(f"[Rerank] {reason}，保持融合排序取 Top {top_n}")
                return candidates[:top_n]
            logger.info(f"[Rerank] {reason}，按向量分截断 Top {top_n}")
            return sorted(candidates, key=lambda x: x.vector_score, reverse=True)[:top_n]

        provider = rerank_provider()
        if provider == "zhipu":
            # 接口单次有条数上限，超出的候选排在后面，本来也轮不到
            pool = candidates[:RERANK_MAX_DOCUMENTS]
            scores = self._cloud_rerank_scores(query, pool, tenant_id)
            if scores is None:
                return degrade("云端重排不可用")
            for chunk, score in zip(pool, scores, strict=True):
                chunk.metadata["rerank_score"] = float(score)
            reranked = sorted(pool, key=lambda x: x.metadata["rerank_score"], reverse=True)[:top_n]
            logger.info(f"[Rerank] 智谱云端重排完成，保留 Top {len(reranked)}")
            return reranked

        reranker = get_cross_encoder()
        if reranker is None:
            return degrade("未启用重排模型")

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

    def _cloud_rerank_scores(
        self, query: str, candidates: list[RetrievedChunk], tenant_id: str | None
    ) -> list[float] | None:
        """调智谱云端重排；任何失败都返回 None，并让后续请求在冷却期内直接跳过。"""
        global _rerank_cooldown_until

        scorer = self.__dict__.get("cloud_reranker")  # 评测/测试可以注入带缓存的打分器
        injected = scorer is not None
        if not injected:
            if time.monotonic() < _rerank_cooldown_until:
                return None
            api_key = _rerank_api_key(tenant_id)
            if not api_key:
                logger.warning("[Rerank] RERANK_PROVIDER=zhipu 但没有可用的智谱密钥，跳过重排")
                return None
            scorer = ZhipuRerankClient(api_key, timeout=_env_float("RERANK_TIMEOUT_SECONDS", 2.0, 0.2, 30.0))
        try:
            started = time.monotonic()
            scores = scorer.score(query, [c.text for c in candidates])
            if len(scores) != len(candidates):
                raise RuntimeError("重排分数条数与候选不一致")
            logger.info(f"[Rerank] 云端重排耗时 {time.monotonic() - started:.2f}s")
            return scores
        except Exception as exc:
            if not injected:
                _rerank_cooldown_until = time.monotonic() + RERANK_COOLDOWN_SECONDS
            logger.warning(f"[Rerank] 云端重排失败，降级为融合排序（{type(exc).__name__}: {exc}）")
            return None

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
        min_score: float | None = None,
    ) -> list[RetrievedChunk]:
        """
        【读取】四阶段 RAG 检索，返回结构化片段（供引用卡片使用）：
        1. 向量宽召回 candidate_k 个候选（默认 50，仅限该租户可见的文档）
        2. 题级门控：向量候选里的最高相似度低于 min_score，说明整个问题在知识库语义范围之外，
           直接整题返回空（拒答）。这是按「题」而不是按「片段」判断：
           逐条过滤会把 BM25 才捞得回来、向量分偏低的正确片段一并砍掉。
        3. 放行后，向量 Top-k 与 BM25 Top-k（同样只含该租户可见的片段）做 RRF 融合
        4. 按 RERANK_PROVIDER 重排并截断 Top-K；重排不可用就保持融合排序

        min_score：None 用平台默认（RAG_MIN_SCORE_DEFAULT，默认 0.45）；0 表示关闭门控；
        学校在工作区设置里配的相似度阈值由调用方传进来，覆盖默认值。

        Example:
            >>> chunks = svc.search_chunks("宿舍门禁", "t1", top_k=2, min_score=0.4)  # doctest: +SKIP
        """
        logger.info(f" [VectorDB] 正在检索与 '{query}' 最相关的片段...")
        candidate_k = _env_int("RERANK_CANDIDATE_K", 50)
        gate = default_min_score() if min_score is None else max(0.0, float(min_score))

        try:
            candidates, query_embedding = self._vector_candidates(query, tenant_id, candidate_k)
            if not candidates:
                return []

            best = max(c.vector_score for c in candidates)
            if gate > 0 and best < gate:
                logger.info(f" [VectorDB] 题级门控拒答：最高向量相似度 {best:.3f} < 阈值 {gate:.2f}")
                return []

            pool, ordered = candidates, False
            if hybrid_enabled():
                try:
                    pool = self._fuse_with_bm25(query, tenant_id, candidates, candidate_k)
                    ordered = True
                except Exception as exc:
                    logger.warning(f" [VectorDB] BM25 融合失败，退回纯向量排序: {exc}")

            top = self.rerank_candidates(query, pool, top_n=top_k, tenant_id=tenant_id, keep_order=ordered)
            self._fill_vector_scores(top, query_embedding, {c.chunk_id for c in candidates})
            logger.info(f" [VectorDB] 检索到 {len(top)} 个相关片段")
            return top
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
        self._bump_corpus_version()
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