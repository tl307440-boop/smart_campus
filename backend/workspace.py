"""每所学校的工作区设置、AI 服务配置与嵌入挂件配置：只读 Postgres，带短时缓存。

三张表由 Next 侧 Prisma 建（frontend/prisma/schema.prisma 的 TenantSettings / TenantAiConfig /
EmbedConfig），写入也只走 Next 的管理接口。这里手写 SQL，改表名或列名必须同步。

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
import secret_box
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
    # 学校心理咨询的联系方式，出现在求助卡片和陪伴回复里；空串表示只给全国热线
    counseling_contact: str = ""


@dataclass(frozen=True)
class SchoolAi:
    """学校在「AI 服务」页配的模型；密钥已解密，空串表示没填。"""

    chat_base_url: str = ""
    chat_model: str = ""
    chat_key: str = ""
    reader_base_url: str = ""
    reader_model: str = ""
    reader_key: str = ""
    map_key: str = ""
    search_key: str = ""
    # off / demo / zhengfang
    jw_mode: str = "off"
    jw_cas_url: str = ""
    jw_base_url: str = ""
    news_urls: tuple[str, ...] = ()
    # 智谱向量化密钥走 embedding_key。local / openai 只预留，后端不加载。
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_key: str = ""


@dataclass(frozen=True)
class EmbedSettings:
    id: str
    tenant_id: str
    daily_limit: int
    enabled: bool


DEFAULT_SETTINGS = WorkspaceSettings()
NO_SCHOOL_AI = SchoolAi()

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


def forget_school_ai(tenant_id: str) -> None:
    """管理员刚保存完就点「测一下」时，不能拿缓存里的旧配置去测。"""
    with _cache_lock:
        _cache.pop(("ai", tenant_id), None)


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
            'SELECT "assistantName", "persona", "queryOnly", "similarityThreshold", "topK", "counselingContact" '
            'FROM tenant_settings WHERE "tenantId" = %s',
            (tenant_id,),
        )
        if row is None:
            return DEFAULT_SETTINGS
        name, persona, query_only, threshold, top_k, counseling = row
        return WorkspaceSettings(
            assistant_name=(name or DEFAULT_SETTINGS.assistant_name).strip(),
            persona=(persona or "").strip(),
            query_only=bool(query_only),
            similarity_threshold=_clamp(float(threshold or 0.0), 0.0, 1.0),
            top_k=int(_clamp(int(top_k or DEFAULT_SETTINGS.top_k), 1, 10)),
            counseling_contact=(counseling or "").strip(),
        )

    return _cached("settings", tenant_id, load, DEFAULT_SETTINGS)


def _decrypt(tenant_id: str, token: str) -> str:
    try:
        return secret_box.decrypt(token or "")
    except Exception as exc:
        # 多半是换过 AUTH_JWT_SECRET：当作没填，退回平台默认，并提示管理员重填
        logger.warning(f"[Workspace] {tenant_id} 的模型密钥解不开，按未配置处理: {exc}")
        return ""


def school_ai(tenant_id: str) -> SchoolAi:
    """取某校自配的对话模型与长文档精读模型；没配或读库失败时全部为空。"""

    def load() -> SchoolAi:
        params = (tenant_id,)
        base_sql = (
            'SELECT "chatBaseUrl", "chatModel", "chatKey", "readerBaseUrl", "readerModel", "readerKey", '
            '"mapKey", "searchKey", "jwMode", "jwCasUrl", "jwBaseUrl", "newsUrls" '
            'FROM tenant_ai_configs WHERE "tenantId" = %s'
        )
        try:
            row = _fetch_one(
                base_sql.replace(
                    '"newsUrls" ',
                    '"newsUrls", "embeddingProvider", "embeddingModel", "embeddingBaseUrl", "embeddingKey" ',
                ),
                params,
            )
            has_embedding = True
        except Exception as exc:
            # 迁移还没跑时列不存在。对话配置仍要能读，向量化先按平台默认。
            logger.warning(f"[Workspace] 向量化配置列还没有，先用平台默认: {exc}")
            row = _fetch_one(base_sql, params)
            has_embedding = False
        if row is None:
            return NO_SCHOOL_AI
        (
            chat_url, chat_model, chat_key, reader_url, reader_model, reader_key,
            map_key, search_key, jw_mode, jw_cas_url, jw_base_url, news_urls,
        ) = row[:12]
        if has_embedding:
            embedding_provider, embedding_model, embedding_base_url, embedding_key = row[12:16]
        else:
            embedding_provider, embedding_model, embedding_base_url, embedding_key = "", "", "", ""
        provider = (embedding_provider or "").strip()
        if provider not in {"", "local", "zhipu", "openai"}:
            provider = ""
        return SchoolAi(
            chat_base_url=(chat_url or "").strip(),
            chat_model=(chat_model or "").strip(),
            chat_key=_decrypt(tenant_id, chat_key),
            reader_base_url=(reader_url or "").strip(),
            reader_model=(reader_model or "").strip(),
            reader_key=_decrypt(tenant_id, reader_key),
            map_key=_decrypt(tenant_id, map_key),
            search_key=_decrypt(tenant_id, search_key),
            jw_mode=(jw_mode or "off").strip(),
            jw_cas_url=(jw_cas_url or "").strip().rstrip("/"),
            jw_base_url=(jw_base_url or "").strip().rstrip("/"),
            news_urls=tuple(line.strip() for line in (news_urls or "").splitlines() if line.strip()),
            embedding_provider=provider,
            embedding_model=(embedding_model or "").strip(),
            embedding_base_url=(embedding_base_url or "").strip().rstrip("/"),
            embedding_key=_decrypt(tenant_id, embedding_key),
        )

    return _cached("ai", tenant_id, load, NO_SCHOOL_AI)


def school_name(tenant_id: str) -> str:
    """学校全称，用来限定地图搜索的范围；查不到时为空。"""

    def load() -> str:
        row = _fetch_one('SELECT "name" FROM tenants WHERE "id" = %s', (tenant_id,))
        return (row[0] or "").strip() if row else ""

    return _cached("name", tenant_id, load, "")


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
