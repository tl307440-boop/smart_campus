"""混合检索（向量 + BM25 + RRF）、题级门控、云端重排降级、OCR 回退的单测。

不连 Chroma 和智谱：用内存里的 FakeCollection 复现 where 过滤，租户隔离是真的在测过滤逻辑。
"""
from __future__ import annotations

import io
import json
import math
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
import vector_service
from hybrid_search import BM25, RerankError, ZhipuRerankClient, rrf_fuse, tokenize
from tenancy import GLOBAL_TENANT
from vector_service import RetrievedChunk, VectorRAGService

TENANT_A = "tenantaaa111"
TENANT_B = "tenantbbb222"


# ---------- 测试替身 ----------

def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.hypot(*a) * math.hypot(*b))


def _matches(meta: dict, where: dict | None) -> bool:
    """只实现代码里用到的三种 where：$in / $eq / $and。"""
    if not where:
        return True
    if "$and" in where:
        return all(_matches(meta, w) for w in where["$and"])
    ((field, cond),) = where.items()
    ((op, value),) = cond.items()
    return meta.get(field) in value if op == "$in" else meta.get(field) == value


class FakeCollection:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.get_calls = 0

    def add(self, documents, embeddings, ids, metadatas) -> None:
        for text, emb, id_, meta in zip(documents, embeddings, ids, metadatas, strict=True):
            self.rows.append({"id": id_, "text": text, "emb": list(emb), "meta": dict(meta)})

    def delete(self, where) -> None:
        self.rows = [r for r in self.rows if not _matches(r["meta"], where)]

    def count(self) -> int:
        return len(self.rows)

    def get(self, where=None, ids=None, include=None, limit=None):
        self.get_calls += 1
        rows = [r for r in self.rows if _matches(r["meta"], where) and (ids is None or r["id"] in ids)]
        rows = rows[:limit] if limit else rows
        out = {"ids": [r["id"] for r in rows]}
        include = include or []
        if "documents" in include:
            out["documents"] = [r["text"] for r in rows]
        if "metadatas" in include:
            out["metadatas"] = [r["meta"] for r in rows]
        if "embeddings" in include:
            out["embeddings"] = [r["emb"] for r in rows]
        return out

    def query(self, query_embeddings, n_results, where=None, include=None):
        rows = [r for r in self.rows if _matches(r["meta"], where)]
        rows.sort(key=lambda r: 1 - _cosine(query_embeddings[0], r["emb"]))
        rows = rows[:n_results]
        return {
            "ids": [[r["id"] for r in rows]],
            "documents": [[r["text"] for r in rows]],
            "metadatas": [[r["meta"] for r in rows]],
            "distances": [[1 - _cosine(query_embeddings[0], r["emb"]) for r in rows]],
        }


QUERY_VECTOR = [1.0, 0.0]


def _unit(cosine: float) -> list[float]:
    """与查询向量 (1, 0) 余弦相似度恰为 cosine 的单位向量。"""
    return [cosine, math.sqrt(1 - cosine**2)]


def _service(rows: list[tuple[str, str, str, float]]) -> VectorRAGService:
    """rows: (片段 id, 文本, 归属租户, 与查询的余弦相似度)。"""
    svc = VectorRAGService.__new__(VectorRAGService)
    svc.collection = FakeCollection()
    svc.embedding_model = MagicMock()
    svc.embedding_model.embed_query.return_value = QUERY_VECTOR
    svc.embedding_model.embed_documents.side_effect = lambda texts: [_unit(0.5) for _ in texts]
    for id_, text, owner, cosine in rows:
        svc.collection.add(
            [text], [_unit(cosine)], [id_], [{"source": f"{id_}.md", "tenant_id": owner, "content_hash": "h"}]
        )
    return svc


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in ("RERANK_PROVIDER", "RERANK_ENABLED", "RAG_MIN_SCORE_DEFAULT", "RETRIEVAL_HYBRID",
                 "RERANK_CANDIDATE_K", "RERANK_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RERANK_PROVIDER", "none")
    monkeypatch.setattr(vector_service, "_rerank_cooldown_until", 0.0)


# ---------- 纯算法 ----------

def test_tokenize_uses_char_bigrams_for_chinese_and_words_for_ascii() -> None:
    assert tokenize("国家奖学金 2025") == ["国家", "家奖", "奖学", "学金", "2025"]
    assert tokenize("A") == ["a"]
    assert tokenize("奖") == ["奖"]


def test_bm25_ranks_matching_document_first_and_skips_zero_scores() -> None:
    bm25 = BM25(["国家奖学金每人每年8000元", "图书馆开放时间", "宿舍门禁二十二点"])
    top = bm25.top("国家奖学金多少钱", 5)
    assert top[0][0] == 0
    assert all(score > 0 for _, score in top)
    assert bm25.top("完全无关的火星话题xyz", 5) == []


def test_bm25_handles_empty_corpus() -> None:
    assert BM25([]).top("奖学金", 3) == []


def test_rrf_rewards_items_ranked_high_in_both_lists() -> None:
    fused = [key for key, _ in rrf_fuse([["a", "b"], ["b", "c"]])]
    assert fused == ["b", "a", "c"]  # b 两边都有；a 在向量名单第 1，排在只在 BM25 名单第 2 的 c 前面
    assert [key for key, _ in rrf_fuse([])] == []


# ---------- 题级门控 ----------

def _scholarship_rows() -> list[tuple[str, str, str, float]]:
    return [
        ("v1", "奖学金评审条件综述", GLOBAL_TENANT, 0.90),
        ("v2", "宿舍管理规定", GLOBAL_TENANT, 0.60),
        # 向量分很低，但关键词（国家奖学金 8000）正中：只有 BM25 找得回来
        ("b1", "国家奖学金标准为每人每年8000元", GLOBAL_TENANT, 0.20),
    ]


def test_bm25_only_chunk_survives_the_gate_when_query_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """向量分低于阈值、只靠 BM25 捞回来的正确片段，不能被门控误杀。"""
    svc = _service(_scholarship_rows())
    # 候选池压到 2：向量只召回 v1、v2，b1 完全进不了向量名单，只能靠 BM25
    monkeypatch.setenv("RERANK_CANDIDATE_K", "2")
    assert [c.chunk_id for c in svc.retrieve_candidates("国家奖学金8000元", GLOBAL_TENANT, k=2)] == ["v1", "v2"]

    chunks = svc.search_chunks("国家奖学金8000元", GLOBAL_TENANT, top_k=3, min_score=0.45)

    assert "b1" in [c.chunk_id for c in chunks]
    rescued = next(c for c in chunks if c.chunk_id == "b1")
    # 引用卡片上的分数是补算的真实余弦，不是 0
    assert rescued.vector_score == pytest.approx(0.20, abs=1e-4)


def test_gate_refuses_whole_question_when_best_vector_score_is_low() -> None:
    svc = _service([("v1", "国家奖学金标准", GLOBAL_TENANT, 0.30), ("v2", "宿舍管理", GLOBAL_TENANT, 0.20)])

    assert svc.search_chunks("国家奖学金标准", GLOBAL_TENANT, top_k=3, min_score=0.45) == []
    # 拒答时连 BM25 都不该去碰，防止关键词撞车把库外闲聊放进来
    assert svc.collection.get_calls == 0


def test_gate_passes_just_above_threshold_and_refuses_just_below() -> None:
    # 不测恰好等于：向量距离是浮点数，1 - (1 - 0.45) 不保证严格等于 0.45
    above = _service([("v1", "国家奖学金标准", GLOBAL_TENANT, 0.4501)])
    below = _service([("v1", "国家奖学金标准", GLOBAL_TENANT, 0.4499)])
    assert [c.chunk_id for c in above.search_chunks("国家奖学金", GLOBAL_TENANT, min_score=0.45)] == ["v1"]
    assert below.search_chunks("国家奖学金", GLOBAL_TENANT, min_score=0.45) == []


def test_default_gate_is_045_and_zero_disables_it() -> None:
    svc = _service([("v1", "国家奖学金标准", GLOBAL_TENANT, 0.40)])

    assert svc.search_chunks("国家奖学金", GLOBAL_TENANT) == []  # 默认 0.45 拒答
    assert [c.chunk_id for c in svc.search_chunks("国家奖学金", GLOBAL_TENANT, min_score=0)] == ["v1"]
    assert [c.chunk_id for c in svc.search_chunks("国家奖学金", GLOBAL_TENANT, min_score=0.3)] == ["v1"]


def test_default_gate_can_be_overridden_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service([("v1", "国家奖学金标准", GLOBAL_TENANT, 0.40)])
    monkeypatch.setenv("RAG_MIN_SCORE_DEFAULT", "0.3")
    assert len(svc.search_chunks("国家奖学金", GLOBAL_TENANT)) == 1


def test_hybrid_switch_falls_back_to_pure_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service(_scholarship_rows())
    monkeypatch.setenv("RETRIEVAL_HYBRID", "false")

    chunks = svc.search_chunks("国家奖学金8000元", GLOBAL_TENANT, top_k=3, min_score=0.45)

    assert [c.chunk_id for c in chunks] == ["v1", "v2", "b1"]  # 纯向量分排序
    assert svc.collection.get_calls == 0


def test_bm25_failure_degrades_to_vector_ranking() -> None:
    svc = _service(_scholarship_rows())
    with patch.object(VectorRAGService, "_bm25_for", side_effect=RuntimeError("boom")):
        chunks = svc.search_chunks("国家奖学金", GLOBAL_TENANT, top_k=2, min_score=0.45)
    assert [c.chunk_id for c in chunks] == ["v1", "v2"]


def test_empty_collection_returns_nothing() -> None:
    assert _service([]).search_chunks("奖学金", GLOBAL_TENANT) == []


# ---------- 多租户隔离 ----------

def _two_school_service() -> VectorRAGService:
    return _service([
        ("g1", "全校通用的奖学金说明", GLOBAL_TENANT, 0.80),
        ("a1", "甲校内部：奖学金评审密码为 AAA", TENANT_A, 0.70),
        ("b1", "乙校内部：奖学金评审密码为 BBB", TENANT_B, 0.70),
    ])


def test_bm25_never_leaks_other_schools_private_chunks() -> None:
    svc = _two_school_service()

    for tenant, mine, theirs in ((TENANT_A, "a1", "b1"), (TENANT_B, "b1", "a1")):
        ids = [c.chunk_id for c in svc.search_chunks("奖学金评审密码", tenant, top_k=10, min_score=0.45)]
        assert mine in ids
        assert theirs not in ids

    # 没有学校身份的全局视角只能看到全局文档
    assert [c.chunk_id for c in svc.search_chunks("奖学金评审密码", GLOBAL_TENANT, top_k=10, min_score=0.45)] == ["g1"]


def test_bm25_cache_is_per_tenant() -> None:
    svc = _two_school_service()
    index_a = svc._bm25_for(TENANT_A)
    index_b = svc._bm25_for(TENANT_B)

    assert sorted(index_a.ids) == ["a1", "g1"]
    assert sorted(index_b.ids) == ["b1", "g1"]
    assert svc._bm25_for(TENANT_A) is index_a  # 没变化就复用，不重建


def test_bm25_idf_only_counts_visible_chunks() -> None:
    """别校的私有片段连词频统计都不该参与：否则能通过分数差异推测别校内容。"""
    svc = _two_school_service()
    assert len(svc._bm25_for(TENANT_A).bm25.tfs) == 2


# ---------- 动态增删同步 ----------

def test_index_document_makes_new_chunks_searchable_by_bm25() -> None:
    svc = _service([("g1", "宿舍管理规定", GLOBAL_TENANT, 0.80)])
    assert svc._bm25_for(TENANT_A).ids == ["g1"]

    svc.index_document("独有关键词 量子校车 每天发车", "upload.txt", TENANT_A)

    assert len(svc._bm25_for(TENANT_A).ids) == 2
    hits = svc._bm25_for(TENANT_A).bm25.top("量子校车", 5)
    assert hits and "量子校车" in svc._bm25_for(TENANT_A).texts[hits[0][0]]


def test_index_document_rebuild_replaces_stale_bm25_text() -> None:
    svc = _service([])
    svc.index_document("旧版：补考费每门50元", "fee.txt", TENANT_A)
    svc._bm25_for(TENANT_A)

    # 内容不同但片段数相同：只靠「片段总数」发现不了变化，必须靠版本号
    svc.index_document("新版：补考费每门80元", "fee.txt", TENANT_A)

    texts = svc._bm25_for(TENANT_A).texts
    assert texts == ["新版：补考费每门80元"]


def test_clear_tenant_drops_its_chunks_from_bm25() -> None:
    svc = _two_school_service()
    assert "a1" in svc._bm25_for(TENANT_A).ids

    svc.clear_tenant(TENANT_A)

    assert svc._bm25_for(TENANT_A).ids == ["g1"]
    assert "b1" in svc._bm25_for(TENANT_B).ids  # 别校不受影响


def test_external_writes_are_detected_by_chunk_count() -> None:
    """另一个进程往同一个 Chroma 里写了片段：版本号变不了，片段总数会变。"""
    svc = _service([("g1", "宿舍管理规定", GLOBAL_TENANT, 0.80)])
    assert svc._bm25_for(TENANT_A).ids == ["g1"]

    svc.collection.add(["外部写入"], [_unit(0.5)], ["x1"], [{"source": "x.md", "tenant_id": TENANT_A, "content_hash": "h"}])

    assert sorted(svc._bm25_for(TENANT_A).ids) == ["g1", "x1"]


def test_bm25_cache_evicts_oldest_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service([("g1", "宿舍管理规定", GLOBAL_TENANT, 0.80)])
    monkeypatch.setattr(vector_service, "_BM25_MAX_TENANTS", 2)
    for tenant in ("tenant1", "tenant2", "tenant3"):
        svc._bm25_for(tenant)
    assert list(svc._bm25_state().indexes) == ["tenant2", "tenant3"]


# ---------- 重排：provider 与降级 ----------

def _ranked_pool() -> list[RetrievedChunk]:
    return [
        RetrievedChunk("融合第一", {"source": "a"}, vector_score=0.9, chunk_id="1"),
        RetrievedChunk("融合第二", {"source": "b"}, vector_score=0.8, chunk_id="2"),
        RetrievedChunk("融合第三", {"source": "c"}, vector_score=0.7, chunk_id="3"),
    ]


class _Scorer:
    def __init__(self, scores=None, error: Exception | None = None) -> None:
        self.scores, self.error, self.calls = scores, error, 0

    def score(self, query, texts):
        self.calls += 1
        if self.error:
            raise self.error
        return self.scores


def _svc_with_scorer(scorer) -> VectorRAGService:
    svc = VectorRAGService.__new__(VectorRAGService)
    svc.cloud_reranker = scorer
    return svc


def test_rerank_provider_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RERANK_PROVIDER")
    assert vector_service.rerank_provider() == "local"  # 旧默认：RERANK_ENABLED 缺省为真
    monkeypatch.setenv("RERANK_ENABLED", "false")
    assert vector_service.rerank_provider() == "none"  # 阿里云现有配置保持关闭
    monkeypatch.setenv("RERANK_PROVIDER", "ZHIPU")
    assert vector_service.rerank_provider() == "zhipu"  # 显式配置优先于旧开关
    monkeypatch.setenv("RERANK_PROVIDER", "bogus")
    assert vector_service.rerank_provider() == "none"


def test_cross_encoder_only_loads_for_local_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "zhipu")
    assert vector_service.get_cross_encoder() is None


def test_zhipu_rerank_reorders_by_cloud_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "zhipu")
    scorer = _Scorer([0.1, 0.9, 0.5])
    top = _svc_with_scorer(scorer).rerank_candidates("问题", _ranked_pool(), top_n=2, keep_order=True)

    assert [c.text for c in top] == ["融合第二", "融合第三"]
    assert top[0].metadata["rerank_score"] == 0.9


@pytest.mark.parametrize("error", [RerankError("HTTP 429"), RerankError("TimeoutError"), RuntimeError("boom")])
def test_zhipu_failure_degrades_to_fusion_order(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "zhipu")
    top = _svc_with_scorer(_Scorer(error=error)).rerank_candidates("问题", _ranked_pool(), top_n=2, keep_order=True)
    assert [c.text for c in top] == ["融合第一", "融合第二"]


def test_zhipu_wrong_score_count_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "zhipu")
    top = _svc_with_scorer(_Scorer([0.9])).rerank_candidates("问题", _ranked_pool(), top_n=2, keep_order=True)
    assert [c.text for c in top] == ["融合第一", "融合第二"]


def test_zhipu_without_key_degrades_without_calling_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "zhipu")
    monkeypatch.setattr(vector_service, "_rerank_api_key", lambda tenant_id: "")
    svc = VectorRAGService.__new__(VectorRAGService)
    with patch("vector_service.ZhipuRerankClient") as client:
        top = svc.rerank_candidates("问题", _ranked_pool(), top_n=2, tenant_id=TENANT_A, keep_order=True)
    client.assert_not_called()
    assert [c.text for c in top] == ["融合第一", "融合第二"]


def test_cloud_failure_opens_cooldown_so_next_requests_skip_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "zhipu")
    monkeypatch.setattr(vector_service, "_rerank_api_key", lambda tenant_id: "k")
    svc = VectorRAGService.__new__(VectorRAGService)
    failing = MagicMock()
    failing.return_value.score.side_effect = RerankError("HTTP 429")
    with patch("vector_service.ZhipuRerankClient", failing):
        svc.rerank_candidates("问题", _ranked_pool(), tenant_id=TENANT_A, keep_order=True)
        svc.rerank_candidates("问题", _ranked_pool(), tenant_id=TENANT_A, keep_order=True)
    assert failing.call_count == 1  # 第二次在冷却期内，直接降级，没再发请求


def test_none_provider_keeps_fusion_order_when_hybrid() -> None:
    svc = VectorRAGService.__new__(VectorRAGService)
    pool = _ranked_pool()
    pool[2].vector_score = 0.99  # 向量分最高却排在融合名单最后
    assert svc.rerank_candidates("问题", pool, top_n=3, keep_order=True)[0].text == "融合第一"
    # 旧行为（纯向量）仍按向量分排序
    assert svc.rerank_candidates("问题", pool, top_n=3, keep_order=False)[0].text == "融合第三"


def test_search_chunks_end_to_end_with_cloud_rerank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "zhipu")
    svc = _service(_scholarship_rows())
    # 云端认为 BM25 才捞回来的片段最相关
    order = {"国家奖学金标准为每人每年8000元": 0.99}
    svc.cloud_reranker = MagicMock()
    svc.cloud_reranker.score.side_effect = lambda q, texts: [order.get(t, 0.1) for t in texts]

    chunks = svc.search_chunks("国家奖学金8000元", GLOBAL_TENANT, top_k=1, min_score=0.45)

    assert [c.chunk_id for c in chunks] == ["b1"]
    assert chunks[0].metadata["rerank_score"] == 0.99


# ---------- 智谱 rerank 客户端 ----------

def _response(payload: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


def test_zhipu_client_parses_scores_in_document_order() -> None:
    payload = {"results": [{"index": 1, "relevance_score": 0.8}, {"index": 0, "relevance_score": 0.2}]}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        scores = ZhipuRerankClient("secret", timeout=2.0).score("问", ["甲", "乙"])

    assert scores == [0.2, 0.8]
    request = urlopen.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer secret"
    assert urlopen.call_args.kwargs["timeout"] == 2.0
    assert json.loads(request.data)["documents"] == ["甲", "乙"]


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(b"")),
        urllib.error.URLError("dns"),
        TimeoutError("timed out"),
    ],
)
def test_zhipu_client_wraps_network_failures(failure: Exception) -> None:
    with patch("urllib.request.urlopen", side_effect=failure), pytest.raises(RerankError) as info:
        ZhipuRerankClient("secret").score("问", ["甲"])
    assert "secret" not in str(info.value)


@pytest.mark.parametrize("payload", [{}, {"results": [{"index": 5, "relevance_score": 1}]}, {"results": [{"index": 0}]}])
def test_zhipu_client_rejects_malformed_payloads(payload: dict) -> None:
    with patch("urllib.request.urlopen", return_value=_response(payload)), pytest.raises(RerankError):
        ZhipuRerankClient("secret").score("问", ["甲"])


def test_zhipu_client_rejects_partial_scores() -> None:
    payload = {"results": [{"index": 0, "relevance_score": 0.5}]}
    with patch("urllib.request.urlopen", return_value=_response(payload)), pytest.raises(RerankError):
        ZhipuRerankClient("secret").score("问", ["甲", "乙"])


def test_zhipu_client_refuses_oversized_batches_without_calling_api() -> None:
    with patch("urllib.request.urlopen") as urlopen, pytest.raises(RerankError):
        ZhipuRerankClient("secret", max_documents=2).score("问", ["a", "b", "c"])
    urlopen.assert_not_called()


# ---------- 扫描件 OCR 回退 ----------

def _blank_pdf(path) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as handle:
        writer.write(handle)


def test_scanned_pdf_falls_back_to_ocr_sidecar(tmp_path) -> None:
    from knowledge import KnowledgeBase

    _blank_pdf(tmp_path / "扫描件.pdf")
    (tmp_path / "ocr").mkdir()
    (tmp_path / "ocr" / "扫描件.txt").write_text("第一条 国家奖学金每人每年8000元", encoding="utf-8")

    kb = KnowledgeBase()
    kb._load_dir(str(tmp_path), source="preloaded", tenant_id=GLOBAL_TENANT)

    # 仍以 PDF 的名字登记，ocr 目录里的文本不会被当成第二份文档
    assert [d.name for d in kb.documents(GLOBAL_TENANT)] == ["扫描件.pdf"]
    assert "8000元" in kb._texts[(GLOBAL_TENANT, "扫描件.pdf")]


def test_scanned_pdf_without_sidecar_is_skipped_instead_of_registered_empty(tmp_path) -> None:
    from knowledge import KnowledgeBase

    _blank_pdf(tmp_path / "扫描件.pdf")
    kb = KnowledgeBase()
    kb._load_dir(str(tmp_path), source="preloaded", tenant_id=GLOBAL_TENANT)
    assert kb.documents(GLOBAL_TENANT) == []


def test_uploaded_pdf_never_uses_sidecar(tmp_path) -> None:
    """租户上传目录不吃 OCR 回退：只有随代码发布的预置文档才信任旁挂文本。"""
    from knowledge import KnowledgeBase

    _blank_pdf(tmp_path / "扫描件.pdf")
    (tmp_path / "ocr").mkdir()
    (tmp_path / "ocr" / "扫描件.txt").write_text("不该被读到", encoding="utf-8")

    kb = KnowledgeBase()
    kb._load_dir(str(tmp_path), source="uploaded", tenant_id=TENANT_A)
    assert kb.documents(TENANT_A) == []


def test_shipped_ocr_sidecars_match_real_scanned_pdfs() -> None:
    """data/ocr/ 里的每份 OCR 文本都要对得上一份真实存在、且确实抽不出文字的 PDF。

    名字对不上时回退不会生效，国家奖学金评审办法那批规定就又成了知识库盲区；
    对应的 PDF 要是已经有文字层，旁挂文本就是多余的第二份，应当删掉。

    湖北省学生资助资金管理办法（85 页）的 OCR 文本有意没放进来：评测里它让有答案题的
    Hit@3 掉了约 10 个百分点，而 5 道 OCR 缺口题的证据都在国家奖学金评审办法里
    （见 eval_rag/results/regression.md）。想上线只需把文本放进 data/ocr/。
    """
    import os

    from file_service import FileService
    from knowledge import _data_dir

    data = _data_dir()
    ocr_dir = os.path.join(data, "ocr")
    sidecars = sorted(n for n in os.listdir(ocr_dir) if n.endswith(".txt"))
    assert sidecars, "data/ocr/ 里应当至少有一份 OCR 文本"
    for name in sidecars:
        pdf = os.path.join(data, os.path.splitext(name)[0] + ".pdf")
        assert os.path.isfile(pdf), f"{name} 找不到同名 PDF，回退不会生效"
        assert not FileService.read_file(pdf).strip(), f"{name} 对应的 PDF 已有文字层，不需要旁挂文本"
