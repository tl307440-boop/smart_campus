"""知识库注册表的租户作用域测试。

向量检索那条路由 test_abc_regressions.py 覆盖；这里盯的是另一条泄露路径：
short_corpus() 会把短文全文整段喂给 Kimi，如果不按租户过滤，
即使向量库隔离做对了，别校的资料照样会进提示词。

需要完整依赖（knowledge -> vector_service -> chromadb）:
    cd backend && pytest test_knowledge_scope.py
"""
from __future__ import annotations

import pytest
from knowledge import KnowledgeBase
from tenancy import GLOBAL_TENANT

A = "tenantaaa111"
B = "tenantbbb222"


@pytest.fixture
def kb() -> KnowledgeBase:
    base = KnowledgeBase()
    # 跳过真实向量库：这里只验证内存注册表的作用域
    base._vector_db_failed = True
    base._register("校规总则.md", "全校通用内容", source="wiki", tenant_id=GLOBAL_TENANT)
    base._register("A校内部通知.txt", "A 校专属内容", source="uploaded", tenant_id=A)
    base._register("B校内部通知.txt", "B 校专属内容", source="uploaded", tenant_id=B)
    return base


def names(kb: KnowledgeBase, tenant: str) -> set[str]:
    return {doc.name for doc in kb.documents(tenant)}


def test_tenant_sees_global_plus_own(kb: KnowledgeBase) -> None:
    assert names(kb, A) == {"校规总则.md", "A校内部通知.txt"}


def test_tenant_does_not_see_other_tenant(kb: KnowledgeBase) -> None:
    assert "B校内部通知.txt" not in names(kb, A)
    assert "A校内部通知.txt" not in names(kb, B)


def test_short_corpus_excludes_other_tenant(kb: KnowledgeBase) -> None:
    """这段文本会原样进 Kimi 的提示词，绝不能混入别校内容。"""
    corpus = kb.short_corpus(A)

    assert "A 校专属内容" in corpus
    assert "全校通用内容" in corpus
    assert "B 校专属内容" not in corpus


def test_same_filename_in_two_tenants_coexists() -> None:
    """两校上传同名文件，各自都应看到自己那份，且内容不串。"""
    base = KnowledgeBase()
    base._vector_db_failed = True
    base._register("手册.pdf", "A 的手册", source="uploaded", tenant_id=A)
    base._register("手册.pdf", "B 的手册", source="uploaded", tenant_id=B)

    assert names(base, A) == {"手册.pdf"}
    assert "A 的手册" in base.short_corpus(A)
    assert "B 的手册" not in base.short_corpus(A)


def test_clear_only_removes_own_uploads(kb: KnowledgeBase) -> None:
    """清空是按租户的：维基、预置文档和别校资料都不受影响。"""
    kb.clear(A)

    assert names(kb, A) == {"校规总则.md"}
    assert names(kb, B) == {"校规总则.md", "B校内部通知.txt"}


def test_clear_does_not_wipe_global_docs(kb: KnowledgeBase) -> None:
    """旧实现会整库删除，导致维基与预置 PDF 要重启进程才回来。"""
    kb.clear(A)
    kb.clear(B)

    assert names(kb, GLOBAL_TENANT) == {"校规总则.md"}
    assert "全校通用内容" in kb.short_corpus(GLOBAL_TENANT)


def test_corpus_over_budget_falls_back_to_vector_search() -> None:
    """短文合起来超预算时返回空串，让调用方改走向量检索。"""
    base = KnowledgeBase()
    base._vector_db_failed = True
    for i in range(5):
        base._register(f"doc{i}.md", "字" * 5000, source="uploaded", tenant_id=A)

    assert base.short_corpus(A) == ""
    assert base.short_corpus(A, budget=100000) != ""


def test_long_documents_stay_out_of_short_corpus() -> None:
    """超长文档只进向量库，不整段塞进提示词。"""
    base = KnowledgeBase()
    base._vector_db_failed = True
    base._register("长文.txt", "长" * 20001, source="uploaded", tenant_id=A)

    assert base.short_corpus(A) == ""
    assert names(base, A) == {"长文.txt"}
