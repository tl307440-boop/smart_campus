"""按用户的提问频率限制。

每次提问都会打 2-3 次大模型，登录用户写个循环就能刷爆账单。
计数放 Redis 而不是进程内字典：多实例部署时进程内计数各算各的，等于没限。
Redis 本来就是图的 checkpointer 依赖，不引入新组件。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import redis
from fastapi import HTTPException, status


@dataclass(frozen=True)
class QuotaRule:
    max_requests: int
    window_seconds: int


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def chat_quota() -> QuotaRule:
    return QuotaRule(
        max_requests=_int_env("CHAT_RATE_LIMIT", 20),
        window_seconds=_int_env("CHAT_RATE_WINDOW_SECONDS", 60),
    )


_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379")
        _client = redis.Redis.from_url(url, decode_responses=True)
    return _client


def consume(key: str, rule: QuotaRule, client: redis.Redis | None = None) -> int:
    """固定窗口计数，返回窗口内已用次数。超限时抛 429。

    INCR 后只在第一次设置 TTL，避免每次请求都把窗口往后推、
    让持续刷请求的人永远不过期。
    """
    conn = client or _redis()
    redis_key = f"ratelimit:{key}"

    used = conn.incr(redis_key)
    if used == 1:
        conn.expire(redis_key, rule.window_seconds)

    if used > rule.max_requests:
        retry_after = conn.ttl(redis_key)
        if retry_after is None or retry_after < 0:
            retry_after = rule.window_seconds
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"提问太频繁了，请 {retry_after} 秒后再试",
            headers={"Retry-After": str(retry_after)},
        )
    return used


def enforce_chat_quota(user_id: str, client: redis.Redis | None = None) -> int:
    return consume(f"chat:{user_id}", chat_quota(), client=client)


def enforce_embed_quota(
    embed_id: str, daily_limit: int, client: redis.Redis | None = None
) -> int:
    """嵌入挂件整体的每日回答上限。挂在公开网页上，换访客 id 就能绕过按人限流，
    所以必须再按挂件兜一道总量。"""
    day = time.strftime("%Y%m%d", time.gmtime())
    rule = QuotaRule(max_requests=max(1, daily_limit), window_seconds=86400)
    return consume(f"embed:{embed_id}:{day}", rule, client=client)
