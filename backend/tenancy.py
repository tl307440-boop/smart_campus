"""知识库的租户可见性规则。

学校即租户。校规维基和 data/ 下的预置文档对所有学校可见，
各校自己上传的资料只有本校能检索到——否则 A 校的内部文件会被 B 校学生问出来。

规则集中在这里，是为了让 vector_service 与 knowledge 两侧用同一套判定，
也便于脱离 ChromaDB 单独测试（见 test_tenancy.py）。
"""
from __future__ import annotations

import re

# 预置文档与维基的归属标记。选这个字面量是因为它不可能与 cuid 形式的租户 id 相同。
GLOBAL_TENANT = "__global__"

# 旧版 Streamlit 界面没有登录，拿不到真实学校。给它单独一个归属，
# 免得它上传的东西出现在任何一所学校的检索结果里。
LEGACY_TENANT = "legacy-streamlit"

# 租户 id 会拼进磁盘路径，收紧到 cuid / uuid 用得到的字符
_SAFE_TENANT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class InvalidTenantError(ValueError):
    """租户 id 不合法。调用方应当拒绝该请求，而不是退回默认值。"""


def normalize_tenant(tenant_id: str) -> str:
    """校验来自令牌的租户 id，顺便挡掉路径穿越。"""
    value = (tenant_id or "").strip()
    if value == GLOBAL_TENANT:
        return value
    if not _SAFE_TENANT.match(value):
        raise InvalidTenantError(f"租户标识不合法: {tenant_id!r}")
    return value


def visible_tenants(tenant_id: str) -> list[str]:
    """某个租户能看到的归属集合：全局文档 + 自己的文档。"""
    tenant = normalize_tenant(tenant_id)
    if tenant == GLOBAL_TENANT:
        return [GLOBAL_TENANT]
    return [GLOBAL_TENANT, tenant]


def visibility_filter(tenant_id: str) -> dict:
    """检索时用的 Chroma where 子句。"""
    return {"tenant_id": {"$in": visible_tenants(tenant_id)}}


def owner_filter(tenant_id: str) -> dict:
    """只命中该租户自己写入的 chunk，用于清空与重建索引。"""
    return {"tenant_id": {"$eq": normalize_tenant(tenant_id)}}


def source_filter(source: str, tenant_id: str) -> dict:
    """定位某租户下的某个文档。

    必须带上 tenant_id：两所学校各自上传同名文件时，
    只按 source 匹配会让后写入的那份覆盖掉前一份的索引。
    """
    return {
        "$and": [
            {"source": {"$eq": source}},
            {"tenant_id": {"$eq": normalize_tenant(tenant_id)}},
        ]
    }


def chunk_metadata(source: str, content_hash: str, tenant_id: str) -> dict:
    """写入向量库的 chunk 元数据。"""
    return {
        "source": source,
        "content_hash": content_hash,
        "tenant_id": normalize_tenant(tenant_id),
    }


def is_visible_to(owner_tenant: str, viewer_tenant: str) -> bool:
    """内存态文档注册表用的同一套判定，保证与向量库过滤结果一致。"""
    return owner_tenant in visible_tenants(viewer_tenant)
