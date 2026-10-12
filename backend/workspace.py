"""每所学校的工作区设置与嵌入挂件配置：只读 Postgres，带短时缓存。

两张表由 Next 侧 Prisma 建（frontend/prisma/schema.prisma 的 TenantSettings / EmbedConfig），
写入也只走 Next 的管理接口。这里手写 SQL，改表名或列名必须同步。

没配 DATABASE_URL（本地只起 FastAPI）或查询失败时一律退回默认设置，
不让配置读取故障拖垮问答主链路。
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import psycopg
from env_config import load_backend_env
from logging_config import get_logger

logger = get_logger(__name__)

load_backend_env()

CACHE_TTL_SECONDS = 60.0
_CONNECT_TIMEOUT_SECONDS = 3


@dataclass(frozen=True)
class WorkspaceSettings:
    assistant_name: str = "小懒"
    persona: str = ""
    query_only: bool = False
    similarity_threshold: float = 0.0
    top_k: int = 3


@dataclass(frozen=True)
class EmbedSettings:
    id: str
    tenant_id: str
    daily_limit: int
    enabled: bool


DEFAULT_SETTINGS = WorkspaceSettings()

_cache: dict[tuple[str, str], tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def _fetch_one(sql: str, params: tuple[Any, ...]) -> tuple[Any, ...] | None:
    url = _database_url()
    if not url:
        return None
    with psycopg.connect(url, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def _cached(kind: str, key: str, loader, fallback):
    """按 (kind, key) 缓存 loader 的结果；出错时缓存 fallback，避免每次请求都去撞挂掉的库。"""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get((kind, key))
        if hit and hit[0] > now:
            return hit[1]
    try:
        value = loader()
    except Exception as exc:
        logger.warning(f"[Workspace] 读取 {kind}:{key} 失败，使用默认值: {exc}")
        value = fallback
    with _cache_lock:
        _cache[(kind, key)] = (now + CACHE_TTL_SECONDS, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def workspace_settings(tenant_id: str) -> WorkspaceSettings:
    """取某校的工作区设置；没有记录时返回默认值。

    Example:
        >>> workspace_settings("__global__").assistant_name
        '小懒'
    """

    def load() -> WorkspaceSettings:
        row = _fetch_one(
            'SELECT "assistantName", "persona", "queryOnly", "similarityThreshold", "topK" '
            'FROM tenant_settings WHERE "tenantId" = %s',
            (tenant_id,),
        )
        if row is None:
            return DEFAULT_SETTINGS
        name, persona, query_only, threshold, top_k = row
        return WorkspaceSettings(
            assistant_name=(name or DEFAULT_SETTINGS.assistant_name).strip(),
            persona=(persona or "").strip(),
            query_only=bool(query_only),
            similarity_threshold=_clamp(float(threshold or 0.0), 0.0, 1.0),
            top_k=int(_clamp(int(top_k or DEFAULT_SETTINGS.top_k), 1, 10)),
        )

    return _cached("settings", tenant_id, load, DEFAULT_SETTINGS)


def embed_settings(embed_id: str) -> EmbedSettings | None:
    """取挂件配置。找不到或读库失败都返回 None，调用方据此拒绝访客请求。"""

    def load() -> EmbedSettings | None:
        row = _fetch_one(
            'SELECT e."id", e."tenantId", e."dailyLimit", e."enabled" AND NOT t."disabled" '
            'FROM embed_configs e JOIN tenants t ON t."id" = e."tenantId" WHERE e."id" = %s',
            (embed_id,),
        )
        if row is None:
            return None
        return EmbedSettings(
            id=row[0], tenant_id=row[1], daily_limit=max(1, int(row[2])), enabled=bool(row[3])
        )

    return _cached("embed", embed_id, load, None)
