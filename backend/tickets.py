"""HITL 人工求助工单闭环（B2B 校方落地核心能力）。

当 AI 拒答、知识库未检索到依据或学生点踩后，学生可以把该问题连同上下文一键转人工求助；
教务/学工老师或管理员在后台回复并可一键将标准答案回流进本校知识库，下次相似问题由 AI 直接秒答。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import chat_history
from logging_config import get_logger

logger = get_logger(__name__)

VALID_CATEGORIES = ("academic", "campus", "tech", "other")
CATEGORY_LABELS: dict[str, str] = {
    "academic": "教务学业",
    "campus": "生活资助",
    "tech": "账号系统",
    "other": "其他",
}
VALID_STATUSES = ("open", "answered", "merged", "closed")

MAX_QUESTION_CHARS = 1000
MAX_CONTEXT_CHARS = 2000
MAX_REPLY_CHARS = 4000


@dataclass(frozen=True)
class Ticket:
    """一条人工求助工单，对应 help_tickets 表及联表展示字段。"""

    id: str
    tenant_id: str
    user_id: str
    message_id: str
    category: str
    question: str
    context_summary: str
    status: str
    reply: str
    replied_by_id: str | None
    replied_at: datetime | None
    merged_at: datetime | None
    created_at: datetime
    updated_at: datetime
    requester_name: str = ""
    requester_student_no: str = ""
    replied_by_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, "其他"),
            "message_id": self.message_id,
            "question": self.question,
            "context_summary": self.context_summary,
            "status": self.status,
            "reply": self.reply,
            "replied_by_name": self.replied_by_name,
            "requester_name": self.requester_name,
            "requester_student_no": self.requester_student_no,
            "replied_at": self.replied_at.isoformat() if self.replied_at else None,
            "merged_at": self.merged_at.isoformat() if self.merged_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def enabled() -> bool:
    return chat_history.enabled()


def _connect():
    return chat_history._connect()


_SELECT_FIELDS = """
    t."id", t."tenantId", t."userId", t."messageId", t."category",
    t."question", t."contextSummary", t."status", t."reply",
    t."repliedById", t."repliedAt", t."mergedAt", t."createdAt", t."updatedAt",
    COALESCE(u."name", '') AS requester_name,
    COALESCE(u."studentNo", '') AS requester_student_no,
    COALESCE(r."name", '') AS replied_by_name
FROM help_tickets t
LEFT JOIN users u ON u."id" = t."userId"
LEFT JOIN users r ON r."id" = t."repliedById"
"""


def _row_to_ticket(row: tuple[Any, ...]) -> Ticket:
    return Ticket(
        id=str(row[0]),
        tenant_id=str(row[1]),
        user_id=str(row[2]),
        message_id=str(row[3] or ""),
        category=str(row[4] or "other"),
        question=str(row[5] or ""),
        context_summary=str(row[6] or ""),
        status=str(row[7] or "open"),
        reply=str(row[8] or ""),
        replied_by_id=str(row[9]) if row[9] else None,
        replied_at=row[10],
        merged_at=row[11],
        created_at=row[12],
        updated_at=row[13],
        requester_name=str(row[14] or ""),
        requester_student_no=str(row[15] or ""),
        replied_by_name=str(row[16] or ""),
    )


def normalize_category(category: str) -> str:
    cleaned = (category or "").strip().lower()
    return cleaned if cleaned in VALID_CATEGORIES else "other"


def create_ticket(
    tenant_id: str,
    user_id: str,
    question: str,
    *,
    category: str = "other",
    message_id: str = "",
    context_summary: str = "",
) -> Ticket:
    """学生发起一条求助工单。"""
    q = (question or "").strip()[:MAX_QUESTION_CHARS]
    if not q:
        raise ValueError("求助问题描述不能为空")
    cat = normalize_category(category)
    summary = (context_summary or "").strip()[:MAX_CONTEXT_CHARS]
    msg_id = (message_id or "").strip()[:64]
    ticket_id = str(uuid.uuid4())

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO help_tickets
                    ("id", "tenantId", "userId", "messageId", "category", "question", "contextSummary", "status")
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'open')
                """,
                (ticket_id, tenant_id, user_id, msg_id, cat, q, summary),
            )
            cur.execute(f'SELECT {_SELECT_FIELDS} WHERE t."id" = %s AND t."tenantId" = %s', (ticket_id, tenant_id))
            row = cur.fetchone()
    if row is None:
        raise RuntimeError("工单写入后未能读回")
    return _row_to_ticket(row)


def list_user_tickets(tenant_id: str, user_id: str, *, limit: int = 50) -> list[Ticket]:
    """查看某个学生自己提交的求助工单。"""
    capped = max(1, min(200, int(limit)))
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT {_SELECT_FIELDS} '
                'WHERE t."tenantId" = %s AND t."userId" = %s '
                'ORDER BY t."createdAt" DESC LIMIT %s',
                (tenant_id, user_id, capped),
            )
            rows = cur.fetchall()
    return [_row_to_ticket(r) for r in rows]


def list_tenant_tickets(tenant_id: str, *, status: str = "", limit: int = 100) -> list[Ticket]:
    """教工/管理员查看本校待处理及历史工单（未处理的置顶）。"""
    capped = max(1, min(300, int(limit)))
    cleaned_status = (status or "").strip().lower()
    with _connect() as conn:
        with conn.cursor() as cur:
            if cleaned_status in VALID_STATUSES:
                cur.execute(
                    f'SELECT {_SELECT_FIELDS} '
                    'WHERE t."tenantId" = %s AND t."status" = %s '
                    'ORDER BY t."createdAt" DESC LIMIT %s',
                    (tenant_id, cleaned_status, capped),
                )
            else:
                cur.execute(
                    f'SELECT {_SELECT_FIELDS} '
                    'WHERE t."tenantId" = %s '
                    """ORDER BY CASE t."status" WHEN 'open' THEN 0 WHEN 'answered' THEN 1 WHEN 'merged' THEN 2 ELSE 3 END, t."createdAt" DESC """
                    'LIMIT %s',
                    (tenant_id, capped),
                )
            rows = cur.fetchall()
    return [_row_to_ticket(r) for r in rows]


def get_ticket(tenant_id: str, ticket_id: str) -> Ticket | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT {_SELECT_FIELDS} WHERE t."id" = %s AND t."tenantId" = %s',
                (ticket_id, tenant_id),
            )
            row = cur.fetchone()
    return _row_to_ticket(row) if row else None


def _notify_student_if_bound(ticket: Ticket) -> None:
    """工单回复后如学生绑定了企微/QQ且开启通知，异步或同步尝试推送提醒。"""
    try:
        import im_scheduler

        im_scheduler.notify_ticket_reply(ticket)
    except Exception as exc:
        logger.warning(f"[Tickets] 工单 {ticket.id} IM 推送提醒失败（不影响回复保存）: {exc}")


def reply_ticket(
    tenant_id: str,
    ticket_id: str,
    replied_by_id: str,
    reply: str,
    *,
    close: bool = False,
) -> Ticket | None:
    """教工/管理员回复工单；已有回流记录且未显式关闭时保留 merged 状态。"""
    answer = (reply or "").strip()[:MAX_REPLY_CHARS]
    if not answer and not close:
        raise ValueError("回复内容不能为空")

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT "status", "reply" FROM help_tickets WHERE "id" = %s AND "tenantId" = %s',
                (ticket_id, tenant_id),
            )
            existing = cur.fetchone()
            if existing is None:
                return None
            prev_status, prev_reply = str(existing[0] or "open"), str(existing[1] or "")
            final_reply = answer or prev_reply
            if close:
                next_status = "closed"
            elif prev_status == "merged":
                next_status = "merged"
            else:
                next_status = "answered"

            cur.execute(
                """
                UPDATE help_tickets
                SET "reply" = %s,
                    "repliedById" = %s,
                    "repliedAt" = NOW(),
                    "status" = %s,
                    "updatedAt" = NOW()
                WHERE "id" = %s AND "tenantId" = %s
                """,
                (final_reply, replied_by_id, next_status, ticket_id, tenant_id),
            )
            cur.execute(f'SELECT {_SELECT_FIELDS} WHERE t."id" = %s AND t."tenantId" = %s', (ticket_id, tenant_id))
            row = cur.fetchone()

    ticket = _row_to_ticket(row) if row else None
    if ticket is not None and ticket.reply:
        _notify_student_if_bound(ticket)
    return ticket


def format_kb_markdown(ticket: Ticket, answer: str, *, title: str = "") -> tuple[str, str]:
    """把人工解答整理成适合学校知识库 RAG 检索的高质量问答文档。"""
    heading = (title or ticket.question).strip().replace("\n", " ")[:60]
    cat_label = CATEGORY_LABELS.get(ticket.category, "其他")
    filename = f"工单答疑_{ticket.id[:8]}.md"
    markdown = (
        f"# 问：{heading}\n\n"
        f"- 分类：{cat_label}\n"
        f"- 来源：人工求助工单闭环（#{ticket.id[:8]}）\n\n"
        f"## 学生原问题\n{ticket.question}\n\n"
        f"## 官方核准解答\n{answer}\n"
    )
    return filename, markdown


def merge_ticket_to_kb(
    tenant_id: str,
    ticket_id: str,
    replied_by_id: str,
    *,
    reply_override: str = "",
    title: str = "",
    kb: Any = None,
) -> tuple[Ticket | None, Any | None]:
    """将工单回复一键回流进学校知识库，并把工单状态标记为 merged。"""
    ticket = get_ticket(tenant_id, ticket_id)
    if ticket is None:
        return None, None

    answer = (reply_override or "").strip()[:MAX_REPLY_CHARS] or ticket.reply.strip()
    if len(answer) < 2:
        raise ValueError("请先填写解答内容再回流进知识库")

    filename, markdown = format_kb_markdown(ticket, answer, title=title)
    if kb is None:
        from knowledge import knowledge_base as default_kb

        kb = default_kb

    doc_info = kb.add_upload(filename, markdown.encode("utf-8"), tenant_id)
    if hasattr(kb, "_schedule_index"):
        kb._schedule_index(tenant_id)

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE help_tickets
                SET "reply" = %s,
                    "repliedById" = COALESCE("repliedById", %s),
                    "repliedAt" = COALESCE("repliedAt", NOW()),
                    "mergedAt" = NOW(),
                    "status" = 'merged',
                    "updatedAt" = NOW()
                WHERE "id" = %s AND "tenantId" = %s
                """,
                (answer, replied_by_id, ticket_id, tenant_id),
            )
            cur.execute(f'SELECT {_SELECT_FIELDS} WHERE t."id" = %s AND t."tenantId" = %s', (ticket_id, tenant_id))
            row = cur.fetchone()

    updated = _row_to_ticket(row) if row else None
    if updated is not None and updated.reply and not ticket.reply:
        _notify_student_if_bound(updated)
    return updated, doc_info
