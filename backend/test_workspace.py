"""工作区设置读取，以及它对问答图的影响（人设、只按资料回答、相似度阈值、来源、自动记忆）。

读库、模型、向量库全部打桩，不需要 Postgres / Redis / API Key:
    cd backend && pytest test_workspace.py
"""
from __future__ import annotations

from unittest.mock import MagicMock

import graph_app
import pytest
import workspace
from vector_service import RetrievedChunk
from workspace import WorkspaceSettings


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch):
    # 节点脱离图单独调用时没有 stream writer
    monkeypatch.setattr(graph_app, "_delta_emitter", lambda: (lambda text: None))
    workspace.clear_cache()
    yield
    workspace.clear_cache()


# ---------- workspace 模块 ----------


def test_defaults_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert workspace.workspace_settings("t1") == workspace.DEFAULT_SETTINGS
    assert workspace.embed_settings("e1") is None


def test_row_is_mapped_and_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace, "_fetch_one", lambda sql, params: ("小文", " 人设 ", True, 1.7, 99, " 心理中心 027-1 ")
    )
    settings = workspace.workspace_settings("t1")

    assert settings == WorkspaceSettings("小文", "人设", True, 1.0, 10, "心理中心 027-1")


def test_result_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = MagicMock(return_value=("小文", "", False, 0.0, 3, ""))
    monkeypatch.setattr(workspace, "_fetch_one", calls)

    workspace.workspace_settings("t1")
    workspace.workspace_settings("t1")
    assert calls.call_count == 1


def test_database_error_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """读库故障不能拖垮问答主链路。"""

    def boom(sql, params):
        raise OSError("connection refused")

    monkeypatch.setattr(workspace, "_fetch_one", boom)
    assert workspace.workspace_settings("t1") == workspace.DEFAULT_SETTINGS
    assert workspace.embed_settings("e1") is None


def test_embed_row_is_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace, "_fetch_one", lambda sql, params: ("e1", "t1", 0, True))
    embed = workspace.embed_settings("e1")

    assert embed == workspace.EmbedSettings(id="e1", tenant_id="t1", daily_limit=1, enabled=True)


# ---------- 图节点 ----------


def _use_settings(monkeypatch: pytest.MonkeyPatch, **overrides) -> WorkspaceSettings:
    settings = WorkspaceSettings(**{**WorkspaceSettings().__dict__, **overrides})
    monkeypatch.setattr(graph_app, "workspace_settings", lambda tenant_id: settings)
    return settings


def _mock_deepseek(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    ds = MagicMock()
    ds.use_mock = False
    ds.chat_with_context.return_value = iter(["依据", "手册"])
    monkeypatch.setattr(graph_app, "get_deepseek", lambda: ds)
    return ds


def test_query_only_routes_chitchat_to_retrieve(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(monkeypatch, query_only=True)
    assert graph_app.route_query({"intent": "DIRECT", "tenant_id": "t1"}) == "retrieve"


def test_normal_mode_keeps_direct_route(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(monkeypatch)
    assert graph_app.route_query({"intent": "DIRECT", "tenant_id": "t1"}) == "generate"


def test_query_only_refuses_without_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """查不到资料时直接拒答，不调模型。"""
    _use_settings(monkeypatch, query_only=True, assistant_name="小文")
    ds = _mock_deepseek(monkeypatch)

    out = graph_app.generate_node(
        {"user_question": "你好", "intent": "DIRECT", "context": "", "tenant_id": "t1", "messages": []}
    )

    assert "小文" in out["final_answer"]
    assert "没有找到" in out["final_answer"]
    ds.chat_with_context.assert_not_called()
    ds.client.chat.completions.create.assert_not_called()


def test_query_only_answers_from_context_even_if_intent_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(monkeypatch, query_only=True)
    _mock_deepseek(monkeypatch)
    source = {"index": 1, "source": "手册.pdf", "snippet": "第三条", "score": 0.8, "rerank_score": None}

    out = graph_app.generate_node({
        "user_question": "门禁几点",
        "intent": "DIRECT",
        "context": "第三条",
        "sources": [source],
        "tenant_id": "t1",
        "messages": [],
    })

    assert out["final_answer"] == "依据手册"
    assert out["messages"][-1]["sources"] == [source]


def test_rag_prompt_receives_persona_and_memories(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(monkeypatch, persona="附上学生处电话")
    ds = _mock_deepseek(monkeypatch)
    monkeypatch.setattr(graph_app.memory_store, "relevant_memories", lambda *a, **k: ["我读大三"])

    graph_app.generate_node({
        "user_question": "奖学金",
        "intent": "RAG",
        "context": "资料",
        "tenant_id": "t1",
        "user_id": "u1",
        "messages": [],
    })

    kwargs = ds.chat_with_context.call_args.kwargs
    assert kwargs["persona"] == "附上学生处电话"
    assert kwargs["memories"] == ["我读大三"]


def test_guest_gets_no_memories(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(monkeypatch)
    ds = _mock_deepseek(monkeypatch)
    lookup = MagicMock(return_value=["不该出现"])
    monkeypatch.setattr(graph_app.memory_store, "relevant_memories", lookup)

    graph_app.generate_node({
        "user_question": "奖学金", "intent": "RAG", "context": "资料",
        "tenant_id": "t1", "user_id": "", "messages": [],
    })

    lookup.assert_not_called()
    assert ds.chat_with_context.call_args.kwargs["memories"] == []


def test_retrieve_applies_threshold_and_returns_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(monkeypatch, top_k=2, similarity_threshold=0.4)
    vdb = MagicMock()
    chunk = RetrievedChunk("门禁 22 点", {"source": "手册.pdf", "rerank_score": 0.9}, vector_score=0.8)
    vdb.search_chunks.return_value = [chunk]
    vdb.format_context.return_value = "门禁 22 点\n(来源: 手册.pdf)"
    monkeypatch.setattr(graph_app, "get_vector_db", lambda *_args, **_kwargs: vdb)
    monkeypatch.setattr(graph_app, "_prepare_index", lambda tenant_id: None)

    out = graph_app.retrieve_node(
        {"user_question": "门禁", "route_strategy": "vectordb", "tenant_id": "t1"}
    )

    vdb.search_chunks.assert_called_once_with("门禁", "t1", top_k=2, min_score=0.4)
    assert out["sources"] == [{
        "index": 1, "source": "手册.pdf", "snippet": "门禁 22 点", "score": 0.8, "rerank_score": 0.9,
    }]


def test_kimi_path_sources_list_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(monkeypatch)
    kimi = MagicMock()
    kimi.extract_knowledge.return_value = "摘录"
    monkeypatch.setattr(graph_app, "get_kimi", lambda: kimi)
    corpus = "\n\n--- 文档来源: a.md ---\n\n甲\n\n--- 文档来源: b.md ---\n\n乙"

    out = graph_app.retrieve_node({
        "user_question": "x", "route_strategy": "kimi",
        "current_doc_content": corpus, "tenant_id": "t1",
    })

    assert [s["source"] for s in out["sources"]] == ["a.md", "b.md"]


def test_search_chunks_filters_by_threshold() -> None:
    from vector_service import VectorRAGService

    svc = VectorRAGService.__new__(VectorRAGService)
    svc.retrieve_candidates = MagicMock(return_value=[
        RetrievedChunk("高", {"source": "a"}, vector_score=0.9),
        RetrievedChunk("低", {"source": "b"}, vector_score=0.2),
    ])
    svc.rerank_candidates = lambda query, candidates, top_n: candidates[:top_n]

    chunks = svc.search_chunks("q", "t1", top_k=3, min_score=0.5)
    assert [c.text for c in chunks] == ["高"]


def test_emotion_node_stores_facts_and_strips_them(monkeypatch: pytest.MonkeyPatch) -> None:
    ds = MagicMock()
    ds.analyze_emotion_and_reply.return_value = {"core_emotion": "平静", "memory_facts": ["我读大三"]}
    monkeypatch.setattr(graph_app, "get_deepseek", lambda: ds)
    store = MagicMock(return_value=["note"])
    monkeypatch.setattr(graph_app.memory_store, "add_memories", store)

    out = graph_app.emotion_node({"user_question": "我读大三", "tenant_id": "t1", "user_id": "u1"})

    store.assert_called_once_with("t1", "u1", ["我读大三"], origin="auto")
    assert "memory_facts" not in out["emotion_data"]


def test_emotion_node_skips_memory_for_guest(monkeypatch: pytest.MonkeyPatch) -> None:
    ds = MagicMock()
    ds.analyze_emotion_and_reply.return_value = {"memory_facts": ["我读大三"]}
    monkeypatch.setattr(graph_app, "get_deepseek", lambda: ds)
    store = MagicMock()
    monkeypatch.setattr(graph_app.memory_store, "add_memories", store)

    graph_app.emotion_node({"user_question": "我读大三", "tenant_id": "t1", "user_id": ""})
    store.assert_not_called()
