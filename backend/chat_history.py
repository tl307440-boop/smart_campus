"""登录用户的会话历史：Postgres 是权威副本，Redis 检查点只是运行时状态。

表由 Next 侧 Prisma 建（frontend/prisma/schema.prisma 的 ChatSession / ChatMessage /
ChatSessionTombstone），读写只走这里的手写 SQL，改表名或列名必须同步。

每条 SQL 都带 tenantId + userId 条件：thread_id 是前端传来的，不能只凭它找会话。
没配 DATABASE_URL 时 enabled() 为 False，调用方应退回只用 Redis 的老路径；
这里的函数出错会直接抛出，由调用方决定是降级还是报错。
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from env_config import load_backend_env
from logging_config import get_logger

logger = get_logger(__name__)

load_backend_env()

DEFAULT_TITLE = "新的对话"
TITLE_MAX_CHARS = 40
LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 100
_CONNECT_TIMEOUT_SECONDS = 3

_MESSAGE_COLUMNS = 'role, content, context, sources, tables, emotion, safety'


@dataclass(frozen=True)
class SessionInfo:
    thread_id: str
    title: str
    updated_at: datetime


@dataclass(frozen=True)
class SummaryJob:
    """一次滚动摘要要做的事：把 (from_seq, upto_seq] 这段消息并进旧摘要。"""

    summary: str
    from_seq: int
    upto_seq: int
    messages: list[dict[str, Any]]


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def enabled() -> bool:
    return bool(_database_url())


def _connect() -> psycopg.Connection:
    return psycopg.connect(_database_url(), connect_timeout=_CONNECT_TIMEOUT_SECONDS)


def make_title(question: str) -> str:
    text = " ".join((question or "").split())
    return text[:TITLE_MAX_CHARS] or DEFAULT_TITLE


def _message_row(row: tuple[Any, ...]) -> dict[str, Any]:
    role, content, context, sources, tables, emotion, safety = row
    return {
        "role": role,
        "content": content,
        "context": context or "",
        "sources": sources or [],
        "tables": tables or [],
        "emotion": emotion,
        "safety": safety,
    }


def _owned(cur: psycopg.Cursor, tenant_id: str, user_id: str, thread_id: str) -> bool:
    cur.execute(
        'SELECT 1 FROM chat_sessions WHERE id = %s AND "tenantId" = %s AND "userId" = %s',
        (thread_id, tenant_id, user_id),
    )
    return cur.fetchone() is not None


def _tombstoned(cur: psycopg.Cursor, tenant_id: str, user_id: str, thread_id: str) -> bool:
    cur.execute(
        'SELECT 1 FROM chat_session_tombstones '
        'WHERE "tenantId" = %s AND "userId" = %s AND "threadId" = %s',
        (tenant_id, user_id, thread_id),
    )
    return cur.fetchone() is not None


def _claim_session(cur: psycopg.Cursor, tenant_id: str, user_id: str, thread_id: str, title: str) -> bool:
    """建会话或刷新 updatedAt，并锁住这一行直到事务结束。

    编号已被别人占用时 ON CONFLICT 的 WHERE 不成立、不返回行，调用方必须放弃写入。
    """
    cur.execute(
        'INSERT INTO chat_sessions (id, "tenantId", "userId", title, "createdAt", "updatedAt") '
        "VALUES (%s, %s, %s, %s, now(), now()) "
        'ON CONFLICT (id) DO UPDATE SET "updatedAt" = now(), '
        "title = CASE WHEN chat_sessions.title = %s THEN EXCLUDED.title ELSE chat_sessions.title END "
        'WHERE chat_sessions."tenantId" = EXCLUDED."tenantId" AND chat_sessions."userId" = EXCLUDED."userId" '
        "RETURNING id",
        (thread_id, tenant_id, user_id, title, DEFAULT_TITLE),
    )
    return cur.fetchone() is not None


def _insert_messages(cur: psycopg.Cursor, thread_id: str, start_seq: int, messages: list[dict[str, Any]]) -> None:
    cur.executemany(
        'INSERT INTO chat_messages (id, "sessionId", seq, role, content, context, sources, tables, emotion, safety) '
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                str(uuid.uuid4()),
                thread_id,
                start_seq + offset,
                message.get("role", ""),
                message.get("content") or "",
                message.get("context") or "",
                Jsonb(message.get("sources") or []),
                Jsonb(message.get("tables") or []),
                Jsonb(message["emotion"]) if message.get("emotion") is not None else None,
                Jsonb(message["safety"]) if message.get("safety") is not None else None,
            )
            for offset, message in enumerate(messages)
        ],
    )


def list_sessions(tenant_id: str, user_id: str, limit: int = LIST_DEFAULT_LIMIT) -> list[SessionInfo]:
    limit = max(1, min(limit, LIST_MAX_LIMIT))
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT id, title, "updatedAt" FROM chat_sessions '
            'WHERE "tenantId" = %s AND "userId" = %s ORDER BY "updatedAt" DESC LIMIT %s',
            (tenant_id, user_id, limit),
        )
        return [SessionInfo(thread_id=r[0], title=r[1], updated_at=r[2]) for r in cur.fetchall()]


def load_messages(tenant_id: str, user_id: str, thread_id: str) -> list[dict[str, Any]] | None:
    """这个人名下的会话返回全部消息；库里没有这一行（或不是他的）返回 None。"""
    with _connect() as conn, conn.cursor() as cur:
        if not _owned(cur, tenant_id, user_id, thread_id):
            return None
        cur.execute(
            f'SELECT {_MESSAGE_COLUMNS} FROM chat_messages WHERE "sessionId" = %s ORDER BY seq',
            (thread_id,),
        )
        return [_message_row(r) for r in cur.fetchall()]


def recent_messages(tenant_id: str, user_id: str, thread_id: str, limit: int) -> list[dict[str, Any]]:
    """最近 limit 条消息（按时间正序），给 Redis 被清空后重新灌回图状态用。"""
    with _connect() as conn, conn.cursor() as cur:
        if not _owned(cur, tenant_id, user_id, thread_id):
            return []
        cur.execute(
            f'SELECT {_MESSAGE_COLUMNS} FROM chat_messages WHERE "sessionId" = %s ORDER BY seq DESC LIMIT %s',
            (thread_id, limit),
        )
        rows = [_message_row(r) for r in cur.fetchall()]
    rows.reverse()
    # 截断后开头若是助手消息，它的提问已经不在了，丢掉免得模型看到半轮
    while rows and rows[0]["role"] != "user":
        rows.pop(0)
    return rows


def is_blocked(tenant_id: str, user_id: str, thread_id: str) -> bool:
    """这个人删过，或者编号已被别人占用：都不能再往里写，接口统一按 404 处理。"""
    with _connect() as conn, conn.cursor() as cur:
        if _tombstoned(cur, tenant_id, user_id, thread_id):
            return True
        cur.execute(
            'SELECT 1 FROM chat_sessions WHERE id = %s AND NOT ("tenantId" = %s AND "userId" = %s)',
            (thread_id, tenant_id, user_id),
        )
        return cur.fetchone() is not None


def drop_last_turn(tenant_id: str, user_id: str, thread_id: str, question: str) -> bool:
    """重新生成 / 改了再问：最后一轮的提问正好是 question 时删掉这一轮。"""
    with _connect() as conn, conn.cursor() as cur:
        if not _owned(cur, tenant_id, user_id, thread_id):
            return False
        cur.execute(
            'SELECT seq, role, content FROM chat_messages WHERE "sessionId" = %s ORDER BY seq DESC LIMIT 2',
            (thread_id,),
        )
        last = cur.fetchall()
        if len(last) != 2 or last[1][1] != "user" or last[1][2] != question or last[0][1] != "assistant":
            return False
        cur.execute('DELETE FROM chat_messages WHERE "sessionId" = %s AND seq >= %s', (thread_id, last[1][0]))
        return True


def summary(tenant_id: str, user_id: str, thread_id: str) -> str:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT summary FROM chat_sessions WHERE id = %s AND "tenantId" = %s AND "userId" = %s',
            (thread_id, tenant_id, user_id),
        )
        row = cur.fetchone()
    return row[0] if row else ""


def record_turn(
    tenant_id: str,
    user_id: str,
    thread_id: str,
    user_message: dict[str, Any],
    assistant_message: dict[str, Any],
) -> int | None:
    """把成功的一轮追加进库，返回最新的 seq；被删过、编号不属于他或重复提交时返回 None。"""
    with _connect() as conn, conn.cursor() as cur:
        if _tombstoned(cur, tenant_id, user_id, thread_id):
            return None
        if not _claim_session(cur, tenant_id, user_id, thread_id, make_title(user_message.get("content", ""))):
            logger.warning(f"[History] 会话编号 {thread_id} 属于别人，这一轮不入库")
            conn.rollback()
            return None
        cur.execute(
            'SELECT seq, role, content FROM chat_messages WHERE "sessionId" = %s ORDER BY seq DESC LIMIT 2',
            (thread_id,),
        )
        last = cur.fetchall()
        max_seq = last[0][0] if last else 0
        if len(last) == 2 and (last[1][1], last[1][2], last[0][1], last[0][2]) == (
            "user", user_message.get("content"), "assistant", assistant_message.get("content"),
        ):
            return None
        _insert_messages(cur, thread_id, max_seq + 1, [user_message, assistant_message])
        return max_seq + 2


def backfill(tenant_id: str, user_id: str, thread_id: str, messages: list[dict[str, Any]]) -> bool:
    """上线前就有、只在 Redis 里的对话，第一次被打开时整段补进库。库里已有消息就不动。"""
    if not messages:
        return False
    first_question = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    with _connect() as conn, conn.cursor() as cur:
        if _tombstoned(cur, tenant_id, user_id, thread_id):
            return False
        if not _claim_session(cur, tenant_id, user_id, thread_id, make_title(first_question)):
            conn.rollback()
            return False
        cur.execute('SELECT 1 FROM chat_messages WHERE "sessionId" = %s LIMIT 1', (thread_id,))
        if cur.fetchone() is not None:
            return False
        _insert_messages(cur, thread_id, 1, messages)
        return True


def delete_session(tenant_id: str, user_id: str, thread_id: str) -> bool:
    """删会话（消息随外键级联）并留删除标记；不是他的或不存在返回 False。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            'DELETE FROM chat_sessions WHERE id = %s AND "tenantId" = %s AND "userId" = %s RETURNING id',
            (thread_id, tenant_id, user_id),
        )
        if cur.fetchone() is None:
            return False
        cur.execute(
            'INSERT INTO chat_session_tombstones ("tenantId", "userId", "threadId") VALUES (%s, %s, %s) '
            "ON CONFLICT DO NOTHING",
            (tenant_id, user_id, thread_id),
        )
        return True


def summary_job(tenant_id: str, user_id: str, thread_id: str, keep_turns: int, batch_turns: int) -> SummaryJob | None:
    """最近 keep_turns 轮之外、还没进摘要的部分攒够 batch_turns 轮才返回任务。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT s.summary, s."summarizedSeq", COALESCE(MAX(m.seq), 0) FROM chat_sessions s '
            'LEFT JOIN chat_messages m ON m."sessionId" = s.id '
            'WHERE s.id = %s AND s."tenantId" = %s AND s."userId" = %s GROUP BY s.id',
            (thread_id, tenant_id, user_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        old_summary, summarized, max_seq = row
        upto = max_seq - 2 * keep_turns
        upto -= upto % 2
        if upto - summarized < 2 * batch_turns:
            return None
        cur.execute(
            f'SELECT {_MESSAGE_COLUMNS} FROM chat_messages '
            'WHERE "sessionId" = %s AND seq > %s AND seq <= %s ORDER BY seq',
            (thread_id, summarized, upto),
        )
        messages = [_message_row(r) for r in cur.fetchall()]
    return SummaryJob(summary=old_summary, from_seq=summarized, upto_seq=upto, messages=messages)


def save_summary(tenant_id: str, user_id: str, thread_id: str, job: SummaryJob, text: str) -> bool:
    """只在摘要进度仍停在 job.from_seq 时写入，两个后台任务撞车时后到的那个作废。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE chat_sessions SET summary = %s, "summarizedSeq" = %s '
            'WHERE id = %s AND "tenantId" = %s AND "userId" = %s AND "summarizedSeq" = %s',
            (text, job.upto_seq, thread_id, tenant_id, user_id, job.from_seq),
        )
        return cur.rowcount == 1
