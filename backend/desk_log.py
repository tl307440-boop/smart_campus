"""给 8300 端口的后台留一条提问记录。

只追加、失败就跳过，不参与回答。后台服务自己来读，键名两边约定为 desk:q:{学校}。
"""
from __future__ import annotations

import json
import os
import time

import redis
from logging_config import get_logger

logger = get_logger(__name__)

QUESTION_KEEP = 2000
QUESTION_CHARS = 500

_client: redis.Redis | None = None


def question_key(tenant_id: str) -> str:
    return f"desk:q:{tenant_id}"


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"),
            decode_responses=True,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
        )
    return _client


def remember_question(tenant_id: str, user_id: str, question: str) -> None:
    """记下这次提问。连不上 Redis 时只记一条日志，不把异常抛回问答。"""
    text = question.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    if not tenant_id or not text:
        return
    entry = {
        "ts": time.time(),
        "user": user_id or "guest",
        "question": text[:QUESTION_CHARS],
        "cut": len(text) > QUESTION_CHARS,
    }
    try:
        client = _redis()
        key = question_key(tenant_id)
        client.lpush(key, json.dumps(entry, ensure_ascii=False))
        client.ltrim(key, 0, QUESTION_KEEP - 1)
    except redis.RedisError as exc:
        logger.warning("提问记录没写上: %s", exc)
