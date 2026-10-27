"""提问记录与回答评价：给管理员「使用情况」页用的数据。

表由 Next 侧 Prisma 建（schema.prisma 的 QuestionLog / MessageFeedback），读写只走这里的手写 SQL，
改表名或列名必须同步。和 chat_history 一样：没配 DATABASE_URL 时 enabled() 为 False；
这里的函数出错直接抛，调用方决定是降级还是报错。记录提问是旁路，调用方要自己兜住异常，不能拖垮回答。

隐私：提问记录不存是谁问的，只分登录用户和访客；情绪倾诉类的提问不存原文。
"""
from __future__ import annotations

import random
import uuid
from typing import Any

import chat_history
import psycopg
from usage_stats import COMFORT, QUESTION_MAX_CHARS

RETENTION_DAYS = 180
# 清理旧记录不用每次都做：平均每 50 次写入顺带清一回
_PURGE_ONE_IN = 50
MAX_ROWS = 3000
REASONS = {"wrong", "outdated", "verbose", "other"}


def enabled() -> bool:
    return chat_history.enabled()


def _connect() -> psycopg.Connection:
    return chat_history._connect()


def record_question(
    tenant_id: str,
    question: str,
    outcome: str,
    tools: list[str],
    guest: bool,
    message_id: str | None,
) -> None:
    text = "" if outcome == COMFORT else " ".join(question.split())[:QUESTION_MAX_CHARS]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO question_logs (id, "tenantId", question, outcome, tools, guest, "messageId") '
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), tenant_id, text, outcome, ",".join(tools), guest, message_id),
        )
        if random.randrange(_PURGE_ONE_IN) == 0:
            cur.execute(
                'DELETE FROM question_logs WHERE "tenantId" = %s AND "createdAt" < now() - make_interval(days => %s)',
                (tenant_id, RETENTION_DAYS),
            )


def set_feedback(tenant_id: str, user_id: str, message_id: str, rating: int, reason: str) -> bool:
    """给这个人名下的一条助手回答打分；rating 为 0 是取消。消息不存在或不是他的返回 False。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM chat_messages m JOIN chat_sessions s ON s.id = m.\"sessionId\" "
            'WHERE m.id = %s AND m.role = \'assistant\' AND s."tenantId" = %s AND s."userId" = %s',
            (message_id, tenant_id, user_id),
        )
        if cur.fetchone() is None:
            return False
        if rating == 0:
            cur.execute('DELETE FROM message_feedbacks WHERE "messageId" = %s AND "userId" = %s', (message_id, user_id))
            return True
        cur.execute(
            'INSERT INTO message_feedbacks (id, "tenantId", "userId", "messageId", rating, reason) '
            "VALUES (%s, %s, %s, %s, %s, %s) "
            'ON CONFLICT ("messageId", "userId") DO UPDATE SET rating = EXCLUDED.rating, '
            'reason = EXCLUDED.reason, "updatedAt" = now()',
            (str(uuid.uuid4()), tenant_id, user_id, message_id, rating, reason if rating == -1 and reason in REASONS else ""),
        )
        return True


def usage_rows(tenant_id: str, days: int) -> list[dict[str, Any]]:
    """最近 days 天的提问，连同点赞点踩，按时间从新到旧。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT q.question, q.outcome, q.tools, q.guest, q."createdAt", f.rating, f.reason, a.content '
            "FROM question_logs q "
            'LEFT JOIN message_feedbacks f ON f."messageId" = q."messageId" '
            'LEFT JOIN chat_messages a ON a.id = q."messageId" '
            'WHERE q."tenantId" = %s AND q."createdAt" >= now() - make_interval(days => %s) '
            'ORDER BY q."createdAt" DESC LIMIT %s',
            (tenant_id, days, MAX_ROWS),
        )
        return [
            {
                "question": question,
                "outcome": outcome,
                "tools": tools,
                "guest": guest,
                "at": at,
                "rating": rating,
                "reason": reason or "",
                "answer": (content or "")[:200],
            }
            for question, outcome, tools, guest, at, rating, reason, content in cur.fetchall()
        ]
