import hashlib
import os
import uuid
from dataclasses import dataclass, field

import chromadb
from langchain_community.embeddings import ZhipuAIEmbeddings

from env_config import load_backend_env


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
        print(f"[Rerank] 正在加载交叉编码器: {model_name}")
        _cross_encoder = CrossEncoder(model_name)
        print("[Rerank] 交叉编码器加载成功")
    except Exception as e:
        print(f"[Rerank] 交叉编码器不可用，将使用向量相似度截断: {e}")
        _cross_encoder = None
    return _cross_encoder


def document_content_hash(content: str) -> str:
    """文档内容指纹，用于判断是否需要重新向量化。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

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


class VectorRAGService:
    """
    方案 B：传统 RAG 引擎 (基于智谱AI + ChromaDB)
    适用场景：超大文档库 (>20万字)、全校校规合集等。
    原理：切片 -> 向量化 -> 检索 Top-K 片段。
    """
    def __init__(self, persist_directory="chroma_db"):
        load_backend_env()

        # 初始化智谱AI向量模型
        print(" [VectorDB] 正在初始化智谱AI向量模型...")
        try:
            self.embedding_model = BatchedZhipuEmbeddings(
                model="embedding-3",
                api_key=os.getenv("zhi_pu_API_KEY"),
                batch_size=64
            )
            print("[VectorDB] 智谱AI向量模型加载成功！")
        except Exception as e:
            print(f"[VectorDB] 连接智谱AI失败: {e}")
            raise
        
        # 初始化持久化向量数据库
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection(
            name="campus_knowledge",
            metadata={"hnsw:space": "cosine"}  # 使用余弦相似度，适合文本向量检索
        )


# // AI辅助生成：DeepSeek-chat (Web), 2026-04
# 包含文本切片算法、UUID 生成及余弦相似度检索逻辑
    def split_text(self, text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list:
        """
        简单的文本切片工具。
        将长文章切成每段 500 字的小块，支持重叠以保持上下文连贯。
        """
        chunks = []
        for i in range(0, len(text), chunk_size - chunk_overlap):
            chunk = text[i : i + chunk_size]
            if chunk:  # 避免空片段
                chunks.append(chunk)
        return chunks
    
    def is_file_exists(self, file_path: str) -> bool:
        """检查其中一个文件是否已经建立过索引"""
        result = self.collection.get(
            where={"source": file_path},
            limit=1
        )
        return len(result['ids']) > 0

    def get_indexed_content_hash(self, file_path: str) -> str | None:
        """读取已索引文档的 content_hash；无记录则返回 None。"""
        result = self.collection.get(
            where={"source": file_path},
            limit=1,
            include=["metadatas"],
        )
        if not result["ids"]:
            return None
        meta = result["metadatas"][0] or {}
        return meta.get("content_hash")

    def index_document(self, content: str, file_path: str):
        """
        【写入】将文档切片并向量化后存入向量库。
        若同 source 且 content_hash 未变，则跳过 embedding；内容变更则删除旧片段后重建。
        """
        content_hash = document_content_hash(content)
        existing_hash = self.get_indexed_content_hash(file_path)
        if existing_hash is not None and existing_hash == content_hash:
            print(f" [VectorDB] 文件 '{file_path}' 内容未变，跳过 API 向量化...")
            return

        if existing_hash is not None:
            print(f" [VectorDB] 文件 '{file_path}' 内容已变更，删除旧索引后重建...")
            self.collection.delete(where={"source": file_path})

        chunks = self.split_text(content)
        print(f" [VectorDB] 正在将文档切分为 {len(chunks)} 个片段并建立索引...")

        ids = [str(uuid.uuid4()) for _ in range(len(chunks))]
        metadata = [{"source": file_path, "content_hash": content_hash} for _ in range(len(chunks))]

        try:
            print(f"[VectorDB] 正在请求智谱 API (消耗 Token)...")
            embeddings = self.embedding_model.embed_documents(chunks)

            self.collection.add(
                documents=chunks,
                embeddings=embeddings,
                ids=ids,
                metadatas=metadata
            )
            print(f"[VectorDB] 成功索引 {len(chunks)} 个文档片段")
        except Exception as e:
            print(f" [VectorDB] 索引失败: {e}")
            raise
    
    def retrieve_candidates(self, query: str, k: int = 50) -> list[RetrievedChunk]:
        """
        第一阶段：宽召回。用智谱 embedding + Chroma 余弦检索捞一批候选 Chunk。
        当前仓库没有独立 BM25/hybrid_retriever，因此用向量宽召回承担「大网捞鱼」。
        """
        total = self.collection.count()
        if total <= 0:
            return []

        n_results = min(k, total)
        query_embedding = self.embedding_model.embed_query(query)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
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
        print(f"[VectorDB] 宽召回 {len(candidates)} 个候选片段 (请求 k={k})")
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
            print(f"[Rerank] 未启用交叉编码器，按向量分截断 Top {top_n}")
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
        print(f"[Rerank] 交叉编码器重排完成，保留 Top {len(reranked)}")
        return reranked

    def _format_context(self, chunks: list[RetrievedChunk]) -> str:
        """把重排后的 Chunk 拼成 generate_node 使用的上下文字符串。"""
        if not chunks:
            return ""

        retrieved_pieces = []
        source_names = set()
        for chunk in chunks:
            source_file = chunk.metadata.get("source", "未知文件")
            source_names.add(source_file)
            retrieved_pieces.append(f"{chunk.text}\n(来源: {source_file})")

        retrieved_context = "\n\n".join(retrieved_pieces)
        retrieved_context += f"\n\n 本次回答共参考了以下文件：{', '.join(source_names)}"
        return retrieved_context

    def search(self, query: str, top_k: int = 3) -> str:
        """
        【读取】三阶段 RAG 检索：
        1. 宽召回 candidate_k 个候选（默认 50）
        2. BGE CrossEncoder 重排序
        3. 截断 Top-K 后拼接 Prompt 上下文
        """
        print(f" [VectorDB] 正在检索与 '{query}' 最相关的片段...")
        candidate_k = _env_int("RERANK_CANDIDATE_K", 50)

        try:
            candidates = self.retrieve_candidates(query, k=candidate_k)
            reranked_top = self.rerank_candidates(query, candidates, top_n=top_k)
            retrieved_context = self._format_context(reranked_top)
            print(f" [VectorDB] 检索到 {len(reranked_top)} 个相关片段")
            return retrieved_context
        except Exception as e:
            print(f" [VectorDB] 检索失败: {e}")
            return ""
    
    def clear_database(self):
        """清空向量数据库"""
        try:
            print("[VectorDB] 正在清空数据库...")
            self.client.delete_collection(name="campus_knowledge")
            self.collection = self.client.get_or_create_collection(
                name="campus_knowledge",
                metadata={"hnsw:space": "cosine"}
            )
            print(" [VectorDB] 数据库已清空")
        except Exception as e:
            print(f" [VectorDB] 清空数据库出错: {e}")
            self.collection = self.client.get_or_create_collection(
                name="campus_knowledge",
                metadata={"hnsw:space": "cosine"}
            )


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