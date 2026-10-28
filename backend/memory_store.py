"""用户长期记忆：Postgres 是权威副本，Redis 只是清单缓存。

历史：最早存在浏览器 localStorage（换设备就没了），后来搬到 Redis hash
（`memory:<tenant>:<user>`，最多 80 条，丢了就真丢）。现在：
- 正文写在 Postgres 的 `user_memories` 表（表由 Next 侧 Prisma 建，见 schema.prisma），
  Redis 只缓存「这一轮该注入哪几条」，缓存过期或 Redis 挂掉都退回直接查库；
- 每条记忆带 `kind` / `slot`：年级、学院、专业这类唯一事实以最新说出的为准，
  新说法就地覆盖旧条目，不让「大三」「大四」同时进提示词；
- 自动抽取时命中隐私（证件、联系方式、心理诊断、经济困难等）一律不落库，
  用户主动说「记住」的照存但标 `sensitive`，此后不自动注入上下文；
- 配上智谱 embedding 后按向量相似度检索，没配就退回中文两字切词匹配。

设计参考 Letta 的分层记忆（核心画像常驻 + 外存按需检索），以及记忆治理里
「冲突靠覆盖而非删除」「隐私在写入时拦截」两条硬约束。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

import psycopg
import redis

from env_config import load_backend_env
from logging_config import get_logger

logger = get_logger(__name__)

load_backend_env()

MAX_MEMORIES = 80
MAX_MEMORY_CHARS = 200
# 始终注入的「核心画像」条数上限，别把提示词撑爆
MAX_CORE_MEMORIES = 3
# 检索结果缓存一小会儿，免得同一轮里 generate 和 finalize 各查一次
CACHE_TTL_SECONDS = 30.0
_CONNECT_TIMEOUT_SECONDS = 3

_REMEMBER_COMMAND = re.compile(r"^\s*记住[:：]\s*(.+)$", re.DOTALL)
_ASCII_WORD = re.compile(r"[a-z0-9]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

# 槽位：同一个人只会有一个年级、一个学院、一个专业，重复提到就覆盖
_SLOT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("grade", re.compile(r"(大[一二三四五六]|研[一二三]|博[一二三四]|高[一二三]|专升本|应届|往届)")),
    ("major", re.compile(r"([\u4e00-\u9fff]{2,12}专业)")),
    ("college", re.compile(r"([\u4e00-\u9fff]{2,10}学院)")),
)

# 敏感信息：自动抽取时一律不落库。宁可少记，也不能把学生的隐私存进提示词里
_SENSITIVE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("credential", re.compile(r"(密码|验证码|密钥|私钥|口令|api[-_ ]?key|token|access[-_ ]?key)", re.I)),
    ("identity", re.compile(r"(身份证|银行卡|社保卡|护照号|户口本|学号|家庭住址|家庭地址)")),
    ("contact", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w-]+\.[\w.]+")),
    ("health", re.compile(r"(抑郁|焦虑症|自杀|轻生|自残|自伤|精神分裂|双相|确诊|病史|住院|吃药|心理咨询|心理疾病)")),
    ("finance", re.compile(r"(贫困|低保|建档立卡|助学贷款|欠债|网贷|借钱|拿不出|交不起)")),
    ("private", re.compile(r"(怀孕|堕胎|流产|家暴|性侵|猥亵|霸凌|处分)")),
    ("orientation", re.compile(r"(同性恋|出柜|性别认同|变性)")),
)

_PREFERENCE_HINTS = re.compile(r"(喜欢|偏好|不爱|讨厌|习惯|倾向于|希望别人|别叫我|叫我)")
_ONGOING_HINTS = re.compile(r"(正在|在准备|在申请|在备考|实习中|打算|计划|目标|想要考)")

_COLUMNS = (
    'id, content, kind, slot, origin, pinned, sensitive, embedding, '
    'EXTRACT(EPOCH FROM "createdAt") AS created_at, '
    'EXTRACT(EPOCH FROM "updatedAt") AS updated_at'
)


@dataclass(frozen=True)
class MemoryNote:
    """一条长期记忆。字段与 `user_memories` 表一一对应。"""

    id: str
    content: str
    created_at: float
    origin: str  # "manual" | "auto" | "consolidated"
    kind: str = "fact"  # "profile" | "preference" | "ongoing" | "fact"
    slot: str = ""  # "grade" / "college" / "major" / ""
    pinned: bool = False
    sensitive: bool = False
    updated_at: float = 0.0
    # 只有检索时用，不返回给前端
    embedding: tuple[float, ...] = ()


@dataclass(frozen=True)
class RememberResult:
    """一次写入的结果：新增了什么、覆盖了哪个槽位、哪些因为隐私没记。"""

    added: list[MemoryNote]
    updated: list[MemoryNote]
    rejected: list[str]


_embedder_factory: Any = None
_tidy_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory-tidy")
_cache: dict[str, tuple[float, list[MemoryNote]]] = {}
_cache_lock = threading.Lock()


# ============================================================
# 连接与开关
# ============================================================

def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def enabled() -> bool:
    """没配 DATABASE_URL（只起 FastAPI 的本地环境）时，记忆功能整体降级为不记。"""
    return bool(_database_url())


def _connect() -> psycopg.Connection:
    return psycopg.connect(_database_url(), connect_timeout=_CONNECT_TIMEOUT_SECONDS)


_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True
        )
    return _client


def _cache_key(tenant_id: str, user_id: str) -> str:
    return f"memory:{tenant_id}:{user_id}"


# ============================================================
# 文本规则：槽位、类型、隐私
# ============================================================

def _clean(content: str) -> str:
    return " ".join(content.split())[:MAX_MEMORY_CHARS]


def _stamp(note: MemoryNote) -> float:
    return note.updated_at or note.created_at


def sensitive_category(text: str) -> str | None:
    """返回命中的敏感类别（credential / health …），没有返回 None。"""
    for name, pattern in _SENSITIVE_RULES:
        if pattern.search(text):
            return name
    return None


def is_sensitive(text: str) -> bool:
    return sensitive_category(text) is not None


def infer_slot(text: str) -> str:
    """从自然语言里认出槽位：年级、专业、学院。认不出返回空串。"""
    for slot, pattern in _SLOT_RULES:
        if pattern.search(text):
            return slot
    return ""


def infer_kind(text: str, slot: str = "") -> str:
    if slot:
        return "profile"
    if _PREFERENCE_HINTS.search(text):
        return "preference"
    if _ONGOING_HINTS.search(text):
        return "ongoing"
    return "fact"


# ============================================================
# 向量：配了智谱就存，没配就只用词匹配
# ============================================================

def set_embedder(factory: Any) -> None:
    """注入一个 `(tenant_id) -> embedder | None` 的工厂，由调用方决定用哪个模型。

    embedder 需要有 `embed_documents(texts)` 和 `embed_query(text)`，与 vector_service 一致。
    测试里可以塞假实现，避免真的去请求智谱。
    """
    global _embedder_factory
    _embedder_factory = factory


def _embedder(tenant_id: str) -> Any:
    if _embedder_factory is None:
        return None
    try:
        return _embedder_factory(tenant_id)
    except Exception as exc:
        logger.warning(f"[记忆] 取向量模型失败，本轮只做词匹配: {exc}")
        return None


def _embed(texts: list[str], tenant_id: str) -> list[tuple[float, ...]]:
    """把文本向量化；没配模型或调用失败时返回空列表，调用方退回词匹配。"""
    embedder = _embedder(tenant_id)
    if embedder is None or not texts:
        return []
    try:
        vectors = embedder.embed_documents(texts)
    except Exception as exc:
        logger.warning(f"[记忆] 向量化失败，退回词匹配: {exc}")
        return []
    return [tuple(float(value) for value in vector) for vector in vectors]


def _embed_query(text: str, tenant_id: str) -> tuple[float, ...] | None:
    embedder = _embedder(tenant_id)
    if embedder is None or not text:
        return None
    try:
        return tuple(float(value) for value in embedder.embed_query(text))
    except Exception as exc:
        logger.warning(f"[记忆] 问题向量化失败，退回词匹配: {exc}")
        return None


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


# ============================================================
# 读写
# ============================================================

def _row(row: tuple[Any, ...]) -> MemoryNote:
    memory_id, content, kind, slot, origin, pinned, sensitive, embedding, created_at, updated_at = row
    return MemoryNote(
        id=memory_id,
        content=content,
        created_at=float(created_at or 0),
        origin=origin,
        kind=kind or "fact",
        slot=slot or "",
        pinned=bool(pinned),
        sensitive=bool(sensitive),
        updated_at=float(updated_at or 0),
        embedding=tuple(float(value) for value in (embedding or ())),
    )


def _invalidate(tenant_id: str, user_id: str) -> None:
    with _cache_lock:
        _cache.pop(f"{tenant_id}:{user_id}", None)
    try:
        _redis().delete(_cache_key(tenant_id, user_id))
    except Exception:
        # 缓存清不掉不影响正确性，下次 TTL 到了自然失效
        pass


def list_memories(
    tenant_id: str, user_id: str, client: Any = None
) -> list[MemoryNote]:
    """按收进记忆的时间倒序返回该用户的全部记忆。

    `client` 参数只为兼容旧测试签名，现在指向的是可选的 psycopg 连接。
    """
    if not tenant_id or not user_id or not enabled():
        return []
    cache_id = f"{tenant_id}:{user_id}"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_id)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return list(cached[1])

    notes: list[MemoryNote] = []
    try:
        with (client or _connect()) as conn, conn.cursor() as cur:
            cur.execute(
                f'SELECT {_COLUMNS} FROM user_memories '
                'WHERE "tenantId" = %s AND "userId" = %s ORDER BY "createdAt" DESC',
                (tenant_id, user_id),
            )
            notes = [_row(item) for item in cur.fetchall()]
    except Exception as exc:
        logger.warning(f"[记忆] 读取失败，本轮不带记忆: {exc}")
        return []

    with _cache_lock:
        _cache[cache_id] = (now, notes)
    return notes


def _insert(cur: psycopg.Cursor, tenant_id: str, user_id: str, note: MemoryNote) -> None:
    cur.execute(
        'INSERT INTO user_memories (id, "tenantId", "userId", content, kind, slot, origin, '
        'pinned, sensitive, embedding, "createdAt", "updatedAt") '
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "to_timestamp(%s), to_timestamp(%s))",
        (
            note.id, tenant_id, user_id, note.content, note.kind, note.slot, note.origin,
            note.pinned, note.sensitive, list(note.embedding),
            note.created_at, note.updated_at or note.created_at,
        ),
    )


def _replace_note(
    cur: psycopg.Cursor, tenant_id: str, user_id: str, note: MemoryNote
) -> None:
    cur.execute(
        'UPDATE user_memories SET content = %s, kind = %s, slot = %s, origin = %s, '
        'sensitive = %s, embedding = %s, "updatedAt" = to_timestamp(%s) '
        'WHERE id = %s AND "tenantId" = %s AND "userId" = %s',
        (
            note.content, note.kind, note.slot, note.origin, note.sensitive,
            list(note.embedding), note.updated_at or note.created_at,
            note.id, tenant_id, user_id,
        ),
    )


def _full(cur: psycopg.Cursor, tenant_id: str, user_id: str) -> int:
    cur.execute(
        'SELECT COUNT(*) FROM user_memories WHERE "tenantId" = %s AND "userId" = %s',
        (tenant_id, user_id),
    )
    return int(cur.fetchone()[0])


def _evict(cur: psycopg.Cursor, tenant_id: str, user_id: str) -> None:
    """超出上限时淘汰：先丢普通的老条目，个人档案其次，置顶的最后。

    旧版本一律丢最旧的，结果最先说的「专业、学院」最先没了——恰恰是最有用的。
    """
    total = _full(cur, tenant_id, user_id)
    need = total - MAX_MEMORIES
    if need <= 0:
        return
    cur.execute(
        'DELETE FROM user_memories WHERE id IN ('
        '  SELECT id FROM user_memories WHERE "tenantId" = %s AND "userId" = %s '
        '  ORDER BY (pinned)::int, (slot <> \'\')::int, "updatedAt" ASC LIMIT %s'
        ')',
        (tenant_id, user_id, need),
    )


def _field(item: Any, name: str) -> str:
    if isinstance(item, str):
        return ""
    if isinstance(item, dict):
        value = item.get(name)
        return value.strip() if isinstance(value, str) else ""
    return ""


def remember(
    tenant_id: str,
    user_id: str,
    items: list[Any],
    origin: str = "manual",
    client: Any = None,
) -> RememberResult:
    """写入若干条记忆。`items` 可以是字符串，也可以是 {content, kind, slot} 字典。

    - 内容完全相同的跳过；
    - 同一个槽位（年级 / 学院 / 专业）已有别的说法时，覆盖那一条，不新增；
    - `origin="auto"` 时命中敏感信息的条目直接丢弃，由调用方决定要不要提示。
    """
    if not tenant_id or not user_id or not enabled():
        return RememberResult([], [], [])

    existing = list_memories(tenant_id, user_id)
    current: dict[str, MemoryNote] = {note.id: note for note in existing}
    seen = {note.content for note in existing}
    now = time.time()

    # 先算出要写什么，再一次性向量化，省得一条条请求智谱
    pending: list[tuple[MemoryNote | None, MemoryNote]] = []
    added: list[MemoryNote] = []
    updated: list[MemoryNote] = []
    rejected: list[str] = []

    for offset, item in enumerate(items):
        content = _clean(item if isinstance(item, str) else _field(item, "content"))
        if not content:
            continue
        flagged = sensitive_category(content)
        if flagged and origin == "auto":
            rejected.append(content)
            continue
        if content in seen:
            continue

        slot = _field(item, "slot") or infer_slot(content)
        kind = _field(item, "kind") or infer_kind(content, slot)
        stamp = now + offset * 1e-3
        holder = next((note for note in current.values() if slot and note.slot == slot), None)
        if holder is not None:
            # 就地覆盖：隐私标记和置顶都保留，一旦沾过隐私就一直按隐私处理
            note = replace(
                holder,
                content=content,
                kind=kind,
                origin=origin,
                sensitive=holder.sensitive or bool(flagged),
                updated_at=stamp,
            )
            seen.discard(holder.content)
            seen.add(content)
            current[note.id] = note
            pending.append((holder, note))
            updated.append(note)
            continue

        note = MemoryNote(
            id=uuid.uuid4().hex,
            content=content,
            created_at=stamp,
            origin=origin,
            kind=kind,
            slot=slot,
            sensitive=bool(flagged),
            updated_at=stamp,
        )
        seen.add(content)
        current[note.id] = note
        pending.append((None, note))
        added.append(note)

    if not pending:
        return RememberResult([], [], rejected)

    # 隐私条目不占向量预算：它们本来就不会进提示词
    vectors = _embed([note.content for _, note in pending if not note.sensitive], tenant_id)
    vector_iter = iter(vectors)
    resolved = [
        (previous, replace(note, embedding=() if note.sensitive else next(vector_iter, ())))
        for previous, note in pending
    ]
    added = [note for previous, note in resolved if previous is None]
    updated = [note for previous, note in resolved if previous is not None]

    try:
        with (client or _connect()) as conn, conn.cursor() as cur:
            for previous, note in resolved:
                if previous is None:
                    _insert(cur, tenant_id, user_id, note)
                else:
                    _replace_note(cur, tenant_id, user_id, note)
            if added:
                _evict(cur, tenant_id, user_id)
            conn.commit()
    except Exception as exc:
        logger.warning(f"[记忆] 写入失败: {exc}")
        return RememberResult([], [], rejected)

    _invalidate(tenant_id, user_id)
    logger.info(f"[记忆] 新增 {len(added)} 条，覆盖 {len(updated)} 条，跳过隐私 {len(rejected)} 条")
    return RememberResult(added, updated, rejected)


def add_memories(
    tenant_id: str,
    user_id: str,
    contents: list[str],
    origin: str = "manual",
    client: Any = None,
) -> list[MemoryNote]:
    """写入若干条记忆（只要字符串的简写版），返回新增或覆盖后的条目。"""
    result = remember(tenant_id, user_id, contents, origin=origin, client=client)
    return result.added + result.updated


def update_memory(
    tenant_id: str,
    user_id: str,
    memory_id: str,
    content: str | None = None,
    pinned: bool | None = None,
    client: Any = None,
) -> MemoryNote | None:
    """改内容或置顶。用户自己改的内容按「手动」算，隐私标记只增不减。"""
    if not tenant_id or not user_id or not enabled():
        return None
    note = next((item for item in list_memories(tenant_id, user_id) if item.id == memory_id), None)
    if note is None:
        return None

    updates: dict[str, Any] = {}
    if content is not None:
        cleaned = _clean(content)
        if not cleaned:
            return None
        # 改过的内容可能从隐私变成普通，但反过来不行：沾过隐私就一直按隐私处理
        vectors = () if note.sensitive or is_sensitive(cleaned) else tuple(_embed([cleaned], tenant_id)[:1])
        updates.update(
            content=cleaned,
            sensitive=note.sensitive or is_sensitive(cleaned),
            origin="manual",
            embedding=vectors if vectors else (),
        )
    if pinned is not None:
        updates["pinned"] = pinned
    if not updates:
        return note

    updated = replace(note, **updates)
    if content is not None:
        updated = replace(updated, updated_at=time.time())
    try:
        with (client or _connect()) as conn, conn.cursor() as cur:
            cur.execute(
                'UPDATE user_memories SET content = %s, kind = %s, slot = %s, origin = %s, '
                'pinned = %s, sensitive = %s, embedding = %s, "updatedAt" = to_timestamp(%s) '
                'WHERE id = %s AND "tenantId" = %s AND "userId" = %s',
                (
                    updated.content, updated.kind, updated.slot, updated.origin,
                    updated.pinned, updated.sensitive, list(updated.embedding),
                    updated.updated_at or updated.created_at, memory_id, tenant_id, user_id,
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.warning(f"[记忆] 修改失败: {exc}")
        return None
    _invalidate(tenant_id, user_id)
    return updated


def delete_memory(tenant_id: str, user_id: str, memory_id: str, client: Any = None) -> bool:
    if not tenant_id or not user_id or not enabled():
        return False
    try:
        with (client or _connect()) as conn, conn.cursor() as cur:
            cur.execute(
                'DELETE FROM user_memories WHERE id = %s AND "tenantId" = %s AND "userId" = %s',
                (memory_id, tenant_id, user_id),
            )
            removed = cur.rowcount > 0
            conn.commit()
    except Exception as exc:
        logger.warning(f"[记忆] 删除失败: {exc}")
        return False
    if removed:
        _invalidate(tenant_id, user_id)
    return removed


def clear_memories(tenant_id: str, user_id: str, client: Any = None) -> int:
    """忘掉这个人的全部记忆，返回删掉的条数。"""
    if not tenant_id or not user_id or not enabled():
        return 0
    try:
        with (client or _connect()) as conn, conn.cursor() as cur:
            cur.execute(
                'DELETE FROM user_memories WHERE "tenantId" = %s AND "userId" = %s',
                (tenant_id, user_id),
            )
            removed = cur.rowcount
            conn.commit()
    except Exception as exc:
        logger.warning(f"[记忆] 清空失败: {exc}")
        return 0
    _invalidate(tenant_id, user_id)
    return int(removed)


# ============================================================
# 检索
# ============================================================

def _terms(text: str) -> set[str]:
    """中文按相邻两字切词，英文数字按词；不引分词库也能做粗粒度匹配。"""
    lowered = text.lower()
    terms = set(_ASCII_WORD.findall(lowered))
    for run in _CJK_RUN.findall(lowered):
        terms.update(run[i : i + 2] for i in range(len(run) - 1))
    return terms


def _score(query: str, query_vector: tuple[float, ...] | None, note: MemoryNote) -> float:
    """词重合度 0-1 与余弦相似度各占一半；没有向量时只看词。"""
    query_terms = _terms(query)
    note_terms = _terms(note.content)
    lexical = len(query_terms & note_terms) / len(query_terms) if query_terms else 0.0
    if query_vector is None or not note.embedding:
        return lexical
    return 0.5 * lexical + 0.5 * max(_cosine(query_vector, note.embedding), 0.0)


def relevant_memories(
    tenant_id: str,
    user_id: str,
    query: str,
    limit: int = 5,
    client: Any = None,
) -> list[str]:
    """挑与问题相关的记忆：先保证核心画像在，再按相似度补足。

    - 核心画像（置顶，或带槽位的个人档案）永远带前几条，模型不用检索也知道「我是谁」；
    - 其余按词重合度 + 向量相似度排序，都不相干时带上最近的几条；
    - 隐私条目只供用户自己在「记忆」页看，不自动进提示词。
    """
    notes = [note for note in list_memories(tenant_id, user_id, client=client) if not note.sensitive]
    if not notes:
        return []

    core = [note for note in notes if note.pinned or (note.slot and note.kind == "profile")]
    core.sort(key=_stamp, reverse=True)
    picked: list[MemoryNote] = []
    used_slots: set[str] = set()
    for note in core:
        if note.slot and note.slot in used_slots:
            continue
        picked.append(note)
        if note.slot:
            used_slots.add(note.slot)
        if len(picked) >= min(MAX_CORE_MEMORIES, limit):
            break

    rest = [note for note in notes if note not in picked]
    if not rest:
        return [note.content for note in picked]

    query_vector = _embed_query(query, tenant_id) if any(note.embedding for note in rest) else None
    scored = [(_score(query, query_vector, note), _stamp(note), note) for note in rest]
    matched = [item for item in scored if item[0] > 0]
    pool = matched or scored
    pool.sort(key=lambda item: (item[0], item[1]), reverse=True)

    remaining = max(limit - len(picked), 0)
    return [note.content for note in picked] + [item[2].content for item in pool[:remaining]]


# ============================================================
# 整理
# ============================================================

def consolidate_memories(tenant_id: str, user_id: str, client: Any = None) -> int:
    """整理历史记忆：补上分类、合并同一槽位的重复说法、去掉内容相同的副本。

    纯规则，不调模型：升级前的记忆没有 kind / slot，年级这类信息可能有好几条，
    合并时以最新说的那条为准。返回改动（删除或改写）的条数。
    """
    if not tenant_id or not user_id or not enabled():
        return 0
    notes = list_memories(tenant_id, user_id)
    if not notes:
        return 0

    changed = 0
    kept_by_slot: dict[str, MemoryNote] = {}
    seen_content: set[str] = set()
    drop: set[str] = set()
    rewrites: list[MemoryNote] = []

    # 从旧到新过一遍，后说的覆盖先说的
    for note in sorted(notes, key=_stamp):
        if note.content in seen_content:
            drop.add(note.id)
            changed += 1
            continue
        seen_content.add(note.content)

        slot = note.slot or infer_slot(note.content)
        kind = infer_kind(note.content, slot) if note.kind == "fact" else note.kind
        # 升级前写进来的记忆没做过隐私判断，这里补一次，免得老数据绕过限制进提示词
        sensitive = note.sensitive or is_sensitive(note.content)
        if slot:
            previous = kept_by_slot.get(slot)
            if previous is not None and previous.content != note.content:
                drop.add(previous.id)
                seen_content.discard(previous.content)
                changed += 1
            kept_by_slot[slot] = note
        if (slot, kind, sensitive) != (note.slot, note.kind, note.sensitive):
            rewrites.append(replace(note, slot=slot, kind=kind, sensitive=sensitive))
            changed += 1

    if not drop and not rewrites:
        return 0
    try:
        with (client or _connect()) as conn, conn.cursor() as cur:
            if drop:
                cur.execute(
                    'DELETE FROM user_memories WHERE "tenantId" = %s AND "userId" = %s AND id = ANY(%s)',
                    (tenant_id, user_id, list(drop)),
                )
            for note in rewrites:
                if note.id in drop:
                    continue
                cur.execute(
                    'UPDATE user_memories SET kind = %s, slot = %s, sensitive = %s '
                    'WHERE id = %s AND "tenantId" = %s AND "userId" = %s',
                    (note.kind, note.slot, note.sensitive, note.id, tenant_id, user_id),
                )
            conn.commit()
    except Exception as exc:
        logger.warning(f"[记忆] 整理失败: {exc}")
        return 0
    _invalidate(tenant_id, user_id)
    logger.info(f"[记忆] 整理了 {changed} 处")
    return changed


def maybe_consolidate(tenant_id: str, user_id: str, client: Any = None) -> int:
    """每天最多整理一次这个人的记忆（升级后的补课，也顺带清掉手动重复）。

    频率标记放 Redis；Redis 不可用就直接整理（整理本身是幂等的）。
    """
    marker = f"{_cache_key(tenant_id, user_id)}:tidy"
    try:
        if not _redis().set(marker, "1", nx=True, ex=24 * 3600):
            return 0
    except Exception:
        pass
    return consolidate_memories(tenant_id, user_id, client=client)


def schedule_tidy(tenant_id: str, user_id: str) -> None:
    """在后台线程里整理记忆，不拖慢这一轮回答。"""
    if not tenant_id or not user_id or not enabled():
        return
    _tidy_executor.submit(maybe_consolidate, tenant_id, user_id)


# ============================================================
# 旧数据迁移
# ============================================================

def migrate_from_redis(tenant_id: str, user_id: str) -> int:
    """把还留在 Redis hash 里的旧记忆搬进 Postgres，搬完删除 hash。

    升级期间同一时刻只有一台实例跑迁移，重复执行也只是把已经搬过的再跳一次。
    """
    if not tenant_id or not user_id or not enabled():
        return 0
    key = _cache_key(tenant_id, user_id)
    try:
        raw = _redis().hgetall(key)
    except Exception:
        return 0
    if not raw:
        return 0

    items: list[Any] = []
    for value in raw.values():
        try:
            payload = json.loads(value)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("content"):
            items.append(
                {
                    "content": payload["content"],
                    "kind": payload.get("kind") or "",
                    "slot": payload.get("slot") or "",
                }
            )
    if not items:
        return 0
    result = remember(tenant_id, user_id, items, origin="manual")
    migrated = len(result.added) + len(result.updated)
    if migrated:
        try:
            _redis().delete(key)
        except Exception:
            pass
        logger.info(f"[记忆] 从 Redis 迁入 {migrated} 条")
    return migrated


def take_remember_command(text: str) -> str | None:
    """识别「记住：xxx」，返回要记的内容。"""
    matched = _REMEMBER_COMMAND.match(text)
    if not matched:
        return None
    content = _clean(matched.group(1))
    return content or None
