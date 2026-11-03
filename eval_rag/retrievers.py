"""各检索方案的实现。向量部分直接调用线上的 VectorRAGService，其余是评测用的对照方案。

方案一览（候选池都是 50 条，和线上 RERANK_CANDIDATE_K 一致）：
- vector          线上阿里云现状：智谱 embedding-3 余弦检索，不重排
- bm25            中文字符二元组 BM25，纯关键词，无需任何模型
- hybrid          向量 + BM25，用 RRF（倒数排名融合）合并
- <reranker>      对上面两种候选池，再用重排模型对 [问题, 片段] 重新打分

所有会花钱 / 花时间的调用（embedding、rerank）都按内容哈希缓存在磁盘，
重复跑评测不会重复计费，结果也完全可复现。
"""
from __future__ import annotations

import hashlib
import json
import pickle
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from hybrid_search import BM25, RRF_K  # 线上用的同一份 BM25 / RRF 参数

from eval_rag.corpus import CACHE_DIR

EVAL_TENANT = "eval-school"
CANDIDATE_K = 50


def _sha(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()


# ---------- 磁盘缓存 ----------

class DiskCache:
    """极简的 {哈希: 值} 缓存，pickle 落盘。只给本机评测用，不跨信任边界。"""

    def __init__(self, name: str):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.path = CACHE_DIR / f"{name}.pkl"
        self.data: dict[str, object] = {}
        if self.path.exists():
            self.data = pickle.loads(self.path.read_bytes())
        self._dirty = False

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self._dirty = True

    def flush(self) -> None:
        if self._dirty:
            self.path.write_bytes(pickle.dumps(self.data))
            self._dirty = False


class TimingLog:
    """记录真实调用的耗时和 token，用于报告里的延迟与成本估算。缓存命中不记。"""

    def __init__(self):
        self.path = CACHE_DIR / "timing.json"
        self.rows: list[dict] = []
        if self.path.exists():
            self.rows = json.loads(self.path.read_text(encoding="utf-8"))

    def add(self, kind: str, seconds: float, tokens: int = 0) -> None:
        self.rows.append({"kind": kind, "seconds": round(seconds, 4), "tokens": tokens})

    def flush(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.rows), encoding="utf-8")

    def median(self, kind: str) -> float | None:
        values = sorted(r["seconds"] for r in self.rows if r["kind"] == kind)
        return values[len(values) // 2] if values else None

    def mean_tokens(self, kind: str) -> float | None:
        values = [r["tokens"] for r in self.rows if r["kind"] == kind and r["tokens"]]
        return sum(values) / len(values) if values else None


# ---------- 向量化（带缓存） ----------

class CachedEmbeddings:
    """包一层线上的 BatchedZhipuEmbeddings：相同文本只请求一次。"""

    def __init__(self, inner, model: str, timing: TimingLog):
        self.inner = inner
        self.model = model
        self.cache = DiskCache(f"embed-{model}")
        self.timing = timing

    def embed_documents(self, texts):
        missing = [t for t in dict.fromkeys(texts) if self.cache.get(_sha(self.model, t)) is None]
        if missing:
            started = time.perf_counter()
            vectors = self.inner.embed_documents(missing)
            self.timing.add("embed_documents", time.perf_counter() - started)
            for text, vector in zip(missing, vectors, strict=True):
                self.cache.set(_sha(self.model, text), vector)
            self.cache.flush()
        return [self.cache.get(_sha(self.model, t)) for t in texts]

    def embed_query(self, text):
        key = _sha(self.model, text)
        vector = self.cache.get(key)
        if vector is None:
            started = time.perf_counter()
            vector = self.inner.embed_query(text)
            self.timing.add("embed_query", time.perf_counter() - started)
            self.cache.set(key, vector)
            self.cache.flush()
        return vector


# ---------- 重排模型 ----------

class ZhipuReranker:
    """智谱云端 rerank 接口。不占本机内存，适合 2G 的线上机器。"""

    name = "zhipu-rerank"
    URL = "https://open.bigmodel.cn/api/paas/v4/rerank"

    def __init__(self, api_key: str, timing: TimingLog):
        self.api_key = api_key
        self.cache = DiskCache("rerank-zhipu")
        self.timing = timing

    def score(self, query: str, texts: list[str]) -> list[float]:
        keys = [_sha(query, t) for t in texts]
        todo = [i for i, k in enumerate(keys) if self.cache.get(k) is None]
        if todo:
            scores = self._request(query, [texts[i] for i in todo])
            for i, s in zip(todo, scores, strict=True):
                self.cache.set(keys[i], s)
            self.cache.flush()
        return [self.cache.get(k) for k in keys]

    def _request(self, query: str, documents: list[str]) -> list[float]:
        # 接口单次最多 128 条文档
        out: list[float] = []
        for start in range(0, len(documents), 100):
            out.extend(self._request_batch(query, documents[start:start + 100]))
        return out

    def _request_batch(self, query: str, documents: list[str]) -> list[float]:
        body = json.dumps(
            {"model": "rerank", "query": query, "documents": documents,
             "top_n": len(documents), "return_documents": False}
        ).encode("utf-8")
        request = urllib.request.Request(
            self.URL, data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        last_error: Exception | None = None
        for attempt in range(4):
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                scores = [0.0] * len(documents)
                for item in payload["results"]:
                    scores[item["index"]] = float(item["relevance_score"])
                tokens = int((payload.get("usage") or {}).get("total_tokens") or 0)
                self.timing.add("rerank_zhipu", time.perf_counter() - started, tokens)
                return scores
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in (429, 500, 502, 503, 504):
                    raise
            except urllib.error.URLError as exc:
                last_error = exc
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"智谱 rerank 连续失败: {last_error}")


class LocalCrossEncoder:
    """线上代码用的那种本地 BGE 交叉编码器（需要 sentence-transformers + torch）。"""

    def __init__(self, model_name: str, timing: TimingLog):
        from sentence_transformers import CrossEncoder

        self.name = f"local:{model_name.split('/')[-1]}"
        self.model_name = model_name
        self.model = CrossEncoder(model_name)
        self.cache = DiskCache(f"rerank-local-{model_name.split('/')[-1]}")
        self.timing = timing

    def score(self, query: str, texts: list[str]) -> list[float]:
        keys = [_sha(query, t) for t in texts]
        todo = [i for i, k in enumerate(keys) if self.cache.get(k) is None]
        if todo:
            started = time.perf_counter()
            scores = self.model.predict([[query, texts[i]] for i in todo])
            self.timing.add(f"rerank_{self.name}", time.perf_counter() - started)
            for i, s in zip(todo, scores, strict=True):
                self.cache.set(keys[i], float(s))
            self.cache.flush()
        return [self.cache.get(k) for k in keys]


# ---------- 索引与检索 ----------

@dataclass(frozen=True)
class Hit:
    text: str
    source: str
    vector_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None


@dataclass
class QueryRun:
    """一个问题在所有方案下的候选与排序。每个列表按该方案的排序从好到坏。"""

    vector: list[Hit] = field(default_factory=list)  # 精确余弦（评测用，可复现）
    ann: list[Hit] = field(default_factory=list)  # 线上实际用的 Chroma HNSW 近似检索，仅用于对比
    bm25: list[Hit] = field(default_factory=list)
    hybrid: list[Hit] = field(default_factory=list)
    # {重排模型名: {"vector": [...], "hybrid": [...]}}
    reranked: dict[str, dict[str, list[Hit]]] = field(default_factory=dict)


class EvalIndex:
    """一份语料的索引。用线上的 VectorRAGService 建库，保证切片与入库逻辑一致。"""

    def __init__(self, variant: str, corpus: dict[str, str], embeddings, persist_dir: Path):
        from tenancy import GLOBAL_TENANT
        from vector_service import VectorRAGService

        self.variant = variant
        self.svc = VectorRAGService(
            persist_directory=str(persist_dir), embeddings=embeddings, collection_name=f"eval_{variant}"
        )
        for name, text in corpus.items():
            if text.strip():
                self.svc.index_document(text, name, GLOBAL_TENANT)
        # 索引是持久化的：语料里去掉的文档要从库里清走，否则旧片段会混进检索结果
        wanted = {name for name, text in corpus.items() if text.strip()}
        stale = {(m or {}).get("source") for m in self.svc.collection.get(include=["metadatas"])["metadatas"]} - wanted
        for source in stale:
            self.svc.collection.delete(where={"source": source})
        if stale:
            self.svc._bump_corpus_version()
        stored = self.svc.collection.get(include=["documents", "metadatas", "embeddings"])
        # 按 (文档, 文本) 排成固定顺序：Chroma 每次返回的顺序不保证一致，
        # 而并列分数时的取舍会受顺序影响，这会让评测结果在两次运行间飘动
        rows = sorted(
            zip(stored["documents"], [(m or {}).get("source", "") for m in stored["metadatas"]],
                stored["embeddings"], strict=True),
            key=lambda r: (r[1], r[0]),
        )
        self.texts: list[str] = [r[0] for r in rows]
        self.sources: list[str] = [r[1] for r in rows]
        matrix = np.asarray([r[2] for r in rows], dtype=np.float32)
        self.matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
        self.bm25 = BM25(self.texts)

    @property
    def chunk_count(self) -> int:
        return len(self.texts)

    def run(self, query: str, rerankers: list, k: int = CANDIDATE_K) -> QueryRun:
        # 线上用 Chroma 的 HNSW 近似检索：每次进程启动重建近邻图，第 50 名附近的候选会有出入。
        # 评测里改用精确余弦（片段只有几百个，暴力算很快），结果才可复现；分数定义与线上相同（1 - 余弦距离）。
        query_vector = np.asarray(self.svc.embedding_model.embed_query(query), dtype=np.float32)
        sims = self.matrix @ (query_vector / np.linalg.norm(query_vector))
        top = np.argsort(-sims, kind="stable")[:k]
        vector = [Hit(self.texts[i], self.sources[i], vector_score=float(sims[i])) for i in top]
        ann = [
            Hit(c.text, c.metadata.get("source", ""), vector_score=c.vector_score)
            for c in self.svc.retrieve_candidates(query, EVAL_TENANT, k=k)
        ]

        scores = self.bm25.scores(query)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        bm25 = [Hit(self.texts[i], self.sources[i], bm25_score=scores[i]) for i in order if scores[i] > 0]

        hybrid = self._fuse(vector, bm25, k)
        run = QueryRun(vector=vector, ann=ann, bm25=bm25, hybrid=hybrid)
        for reranker in rerankers:
            run.reranked[reranker.name] = {
                "vector": self._rerank(reranker, query, vector),
                "hybrid": self._rerank(reranker, query, hybrid),
            }
        return run

    def _fuse(self, vector: list[Hit], bm25: list[Hit], k: int) -> list[Hit]:
        """RRF：每个列表里排名靠前的得分高，两份名单的得分相加。"""
        fused: dict[tuple[str, str], float] = {}
        for ranking in (vector, bm25):
            for rank, hit in enumerate(ranking, start=1):
                key = (hit.source, hit.text)
                fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
        vector_scores = {(h.source, h.text): h.vector_score for h in vector}
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [
            Hit(text, source, vector_score=vector_scores.get((source, text)), rrf_score=score)
            for (source, text), score in ranked
        ]

    @staticmethod
    def _rerank(reranker, query: str, candidates: list[Hit]) -> list[Hit]:
        if not candidates:
            return []
        scores = reranker.score(query, [c.text for c in candidates])
        scored = [replace(c, rerank_score=s) for c, s in zip(candidates, scores, strict=True)]
        return sorted(scored, key=lambda h: h.rerank_score, reverse=True)
