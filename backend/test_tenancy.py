"""租户可见性回归测试。

不依赖 ChromaDB，只验证规则本身:
    cd backend && pytest test_tenancy.py
"""
from __future__ import annotations

import pytest
from tenancy import (
    GLOBAL_TENANT,
    InvalidTenantError,
    chunk_metadata,
    is_visible_to,
    normalize_tenant,
    owner_filter,
    source_filter,
    visibility_filter,
    visible_tenants,
)

A = "tenantaaa111"
B = "tenantbbb222"


# ---------- 可见范围 ----------


def test_tenant_sees_global_and_self_only() -> None:
    assert visible_tenants(A) == [GLOBAL_TENANT, A]


def test_tenant_cannot_see_another_tenant() -> None:
    assert is_visible_to(A, A) is True
    assert is_visible_to(GLOBAL_TENANT, A) is True
    # 这一条是整套隔离的核心：B 校的资料对 A 校不可见
    assert is_visible_to(B, A) is False
    assert is_visible_to(A, B) is False


def test_global_context_sees_only_global() -> None:
    """预载阶段以全局身份运行，不应顺带看到任何学校的上传。"""
    assert visible_tenants(GLOBAL_TENANT) == [GLOBAL_TENANT]
    assert is_visible_to(A, GLOBAL_TENANT) is False


# ---------- Chroma 过滤器 ----------


def test_visibility_filter_covers_global_and_self() -> None:
    assert visibility_filter(A) == {"tenant_id": {"$in": [GLOBAL_TENANT, A]}}


def test_owner_filter_excludes_global() -> None:
    """清空必须只删本租户的片段，不能连维基和预置文档一起删。"""
    f = owner_filter(A)
    assert f == {"tenant_id": {"$eq": A}}
    assert GLOBAL_TENANT not in str(f)


def test_source_filter_is_scoped_by_tenant() -> None:
    """两校上传同名文件时，只按 source 匹配会互相覆盖索引。"""
    fa = source_filter("手册.pdf", A)
    fb = source_filter("手册.pdf", B)
    assert fa != fb
    assert {"tenant_id": {"$eq": A}} in fa["$and"]
    assert {"source": {"$eq": "手册.pdf"}} in fa["$and"]


def test_chunk_metadata_carries_owner() -> None:
    meta = chunk_metadata("手册.pdf", "abc123", A)
    assert meta == {"source": "手册.pdf", "content_hash": "abc123", "tenant_id": A}


# ---------- 输入校验 ----------


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "../../etc", "a/b", "a\\b", "tenant id", "x" * 65, "租户"],
)
def test_illegal_tenant_is_rejected(bad: str) -> None:
    """租户 id 会拼进磁盘路径，必须挡掉穿越与分隔符。"""
    with pytest.raises(InvalidTenantError):
        normalize_tenant(bad)


def test_normalize_trims_and_accepts_cuid_shape() -> None:
    assert normalize_tenant("  cmf3x9k2a0000abcd  ") == "cmf3x9k2a0000abcd"
    assert normalize_tenant(GLOBAL_TENANT) == GLOBAL_TENANT


def test_filters_reject_illegal_tenant() -> None:
    """过滤器构造也要校验，避免非法值绕过 normalize 直接进查询。"""
    for build in (visibility_filter, owner_filter):
        with pytest.raises(InvalidTenantError):
            build("../evil")
