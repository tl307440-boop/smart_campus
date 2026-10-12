"""用户长期记忆：按 (学校, 用户) 存在 Redis 里，回答前挑相关条目注入提示词。

之前记忆只在浏览器 localStorage，换台设备就没了，且只有自带 Key 时才生效。
存在服务端后两条回答路径（闲聊 / 知识库）都能用上，也能由情感分析那次调用顺带自动抽取。

存储结构：一个 Redis hash，field = 记忆 id，value = JSON。条数有上限，超出时丢最旧的。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass

import redis

MAX_MEMORIES = 80
MAX_MEMORY_CHARS = 200
_REMEMBER_COMMAND = re.compile(r"^\s*记住[:：]\s*(.+)$", re.DOTALL)
_ASCII_WORD = re.compile(r"[a-z0-9]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class MemoryNote:
    id: str
    content: str
    created_at: float
    origin: str  # "manual" | "auto"


_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True
        )
    return _client


def _key(tenant_id: str, user_id: str) -> str:
    return f"memory:{tenant_id}:{user_id}"


def _clean(content: str) -> str:
    return " ".join(content.split())[:MAX_MEMORY_CHARS]


def list_memories(
    tenant_id: str, user_id: str, client: redis.Redis | None = None
) -> list[MemoryNote]:
    """按时间倒序返回该用户的全部记忆。"""
    raw = (client or _redis()).hgetall(_key(tenant_id, user_id))
    notes: list[MemoryNote] = []
    for value in raw.values():
        try:
            notes.append(MemoryNote(**json.loads(value)))
        except (TypeError, ValueError):
            continue
    return sorted(notes, key=lambda note: note.created_at, reverse=True)


def add_memories(
    tenant_id: str,
    user_id: str,
    contents: list[str],
    origin: str = "manual",
    client: redis.Redis | None = None,
) -> list[MemoryNote]:
    """写入若干条记忆，内容相同的只保留一条；返回实际新增的条目。

    Example:
        >>> add_memories("t1", "u1", ["我是计算机学院的"])  # doctest: +SKIP
        [MemoryNote(id='...', content='我是计算机学院的', ...)]
    """
    conn = client or _redis()
    existing = list_memories(tenant_id, user_id, client=conn)
    seen = {note.content for note in existing}
    added: list[MemoryNote] = []
    now = time.time()
    for offset, item in enumerate(contents):
        content = _clean(item)
        if not content or content in seen:
            continue
        seen.add(content)
        # 同一批写入时间相同，加个微小偏移保证排序稳定
        added.append(MemoryNote(uuid.uuid4().hex, content, now + offset * 1e-3, origin))

    if not added:
        return []

    key = _key(tenant_id, user_id)
    conn.hset(key, mapping={note.id: json.dumps(asdict(note), ensure_ascii=False) for note in added})

    overflow = (existing + added)
    if len(overflow) > MAX_MEMORIES:
        oldest = sorted(overflow, key=lambda note: note.created_at)[: len(overflow) - MAX_MEMORIES]
        conn.hdel(key, *[note.id for note in oldest])
    return added


def delete_memory(
    tenant_id: str, user_id: str, memory_id: str, client: redis.Redis | None = None
) -> bool:
    return bool((client or _redis()).hdel(_key(tenant_id, user_id), memory_id))


def _terms(text: str) -> set[str]:
    """中文按相邻两字切词，英文数字按词；不引分词库也能做粗粒度匹配。"""
    lowered = text.lower()
    terms = set(_ASCII_WORD.findall(lowered))
    for run in _CJK_RUN.findall(lowered):
        terms.update(run[i : i + 2] for i in range(len(run) - 1))
    return terms


def relevant_memories(
    tenant_id: str,
    user_id: str,
    query: str,
    limit: int = 5,
    client: redis.Redis | None = None,
) -> list[str]:
    """挑与问题重合度最高的几条；都不重合时带上最近几条，让模型始终知道「我是谁」。"""
    notes = list_memories(tenant_id, user_id, client=client)
    if not notes:
        return []
    query_terms = _terms(query)
    scored = [(len(query_terms & _terms(note.content)), note) for note in notes]
    matched = [item for item in scored if item[0] > 0]
    pool = matched or scored
    pool.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
    return [note.content for _, note in pool[:limit]]


def take_remember_command(text: str) -> str | None:
    """识别「记住：xxx」，返回要记的内容。"""
    matched = _REMEMBER_COMMAND.match(text)
    if not matched:
        return None
    content = _clean(matched.group(1))
    return content or None
