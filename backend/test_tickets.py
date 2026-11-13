"""HITL 人工求助工单与知识库回流单元测试。"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import tickets


class FakeTicketDb:
    """内存版 help_tickets 存储，覆盖 tickets.py 的 Postgres 查询。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.seq = 0

    def connect(self):
        db = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def cursor(self):
                return Cursor(db)

        return Conn()


class Cursor:
    def __init__(self, db: FakeTicketDb) -> None:
        self.db = db
        self._rows: list[tuple] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def _to_tuple(self, item: dict) -> tuple:
        return (
            item["id"],
            item["tenantId"],
            item["userId"],
            item["messageId"],
            item["category"],
            item["question"],
            item["contextSummary"],
            item["status"],
            item["reply"],
            item["repliedById"],
            item["repliedAt"],
            item["mergedAt"],
            item["createdAt"],
            item["updatedAt"],
            item["requester_name"],
            item["requester_student_no"],
            item["replied_by_name"],
        )

    def execute(self, sql: str, params: tuple = ()) -> None:
        compact = " ".join(sql.split())
        now = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)

        if compact.startswith("INSERT INTO help_tickets"):
            tid, tenant_id, user_id, msg_id, cat, q, summary = params
            self.db.seq += 1
            self.db.rows[tid] = {
                "id": tid,
                "tenantId": tenant_id,
                "userId": user_id,
                "messageId": msg_id,
                "category": cat,
                "question": q,
                "contextSummary": summary,
                "status": "open",
                "reply": "",
                "repliedById": None,
                "repliedAt": None,
                "mergedAt": None,
                "createdAt": datetime(2026, 11, 13, 9, self.db.seq % 60, tzinfo=timezone.utc),
                "updatedAt": now,
                "requester_name": f"同学{user_id}",
                "requester_student_no": f"2026{user_id[-2:]}",
                "replied_by_name": "",
            }
            self.rowcount = 1
            self._rows = []
            return

        if compact.startswith('SELECT "status", "reply" FROM help_tickets WHERE "id" = %s AND "tenantId" = %s'):
            tid, tenant_id = params
            item = self.db.rows.get(tid)
            if item and item["tenantId"] == tenant_id:
                self._rows = [(item["status"], item["reply"])]
            else:
                self._rows = []
            return

        if compact.startswith('UPDATE help_tickets SET "reply" = %s, "repliedById" = %s'):
            reply, replied_by_id, next_status, tid, tenant_id = params
            item = self.db.rows.get(tid)
            if item and item["tenantId"] == tenant_id:
                item["reply"] = reply
                item["repliedById"] = replied_by_id
                item["repliedAt"] = now
                item["status"] = next_status
                item["updatedAt"] = now
                item["replied_by_name"] = f"老师{replied_by_id}"
                self.rowcount = 1
            else:
                self.rowcount = 0
            self._rows = []
            return

        if compact.startswith('UPDATE help_tickets SET "reply" = %s, "repliedById" = COALESCE'):
            answer, replied_by_id, tid, tenant_id = params
            item = self.db.rows.get(tid)
            if item and item["tenantId"] == tenant_id:
                item["reply"] = answer
                item["repliedById"] = item["repliedById"] or replied_by_id
                item["repliedAt"] = item["repliedAt"] or now
                item["mergedAt"] = now
                item["status"] = "merged"
                item["updatedAt"] = now
                item["replied_by_name"] = item["replied_by_name"] or f"老师{replied_by_id}"
                self.rowcount = 1
            else:
                self.rowcount = 0
            self._rows = []
            return

        if 'WHERE t."id" = %s AND t."tenantId" = %s' in compact:
            tid, tenant_id = params
            item = self.db.rows.get(tid)
            self._rows = [self._to_tuple(item)] if item and item["tenantId"] == tenant_id else []
            return

        if 'WHERE t."tenantId" = %s AND t."userId" = %s' in compact:
            tenant_id, user_id, limit = params
            matched = [
                r for r in self.db.rows.values() if r["tenantId"] == tenant_id and r["userId"] == user_id
            ]
            matched.sort(key=lambda r: r["createdAt"], reverse=True)
            self._rows = [self._to_tuple(r) for r in matched[:limit]]
            return

        if 'WHERE t."tenantId" = %s AND t."status" = %s' in compact:
            tenant_id, status, limit = params
            matched = [
                r for r in self.db.rows.values() if r["tenantId"] == tenant_id and r["status"] == status
            ]
            matched.sort(key=lambda r: r["createdAt"], reverse=True)
            self._rows = [self._to_tuple(r) for r in matched[:limit]]
            return

        if 'WHERE t."tenantId" = %s ORDER BY CASE' in compact:
            tenant_id, limit = params
            order_rank = {"open": 0, "answered": 1, "merged": 2, "closed": 3}
            matched = [r for r in self.db.rows.values() if r["tenantId"] == tenant_id]
            matched.sort(key=lambda r: (order_rank.get(r["status"], 9), -r["createdAt"].timestamp()))
            self._rows = [self._to_tuple(r) for r in matched[:limit]]
            return

        raise AssertionError(f"Unexpected SQL: {compact}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> FakeTicketDb:
    db = FakeTicketDb()
    monkeypatch.setattr(tickets, "_connect", db.connect)
    monkeypatch.setattr(tickets, "_notify_student_if_bound", lambda t: None)
    return db


def test_create_list_and_tenant_user_isolation(fake_db: FakeTicketDb) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        tickets.create_ticket("t1", "u1", "   ")

    t_u1 = tickets.create_ticket(
        "t1",
        "u1",
        "缓考证明需要教务处盖章吗？",
        category="academic",
        message_id="msg-1",
        context_summary="原 AI 回答未查到院系盖章流程",
    )
    t_u2 = tickets.create_ticket("t1", "u2", "宿舍门禁几点关闭？", category="invalid-cat")
    t_other = tickets.create_ticket("t2", "u1", "另一所学校的提问")

    assert t_u1.status == "open"
    assert t_u1.category == "academic"
    assert t_u2.category == "other"  # 非法分类安全回退为 other

    # 学生只看到自己的工单
    u1_list = tickets.list_user_tickets("t1", "u1")
    assert [t.id for t in u1_list] == [t_u1.id]

    # 跨租户不可读
    assert tickets.get_ticket("t2", t_u1.id) is None
    assert tickets.get_ticket("t1", t_other.id) is None

    # 先回复 t_u1，验证管理员列表把 open 置顶
    tickets.reply_ticket("t1", t_u1.id, "staff-1", "需学院教学秘书初审后再到教务处盖章。")
    all_t1 = tickets.list_tenant_tickets("t1")
    assert [t.id for t in all_t1] == [t_u2.id, t_u1.id]
    assert [t.id for t in tickets.list_tenant_tickets("t1", status="answered")] == [t_u1.id]


def test_reply_ticket_updates_status_and_triggers_im_notify(
    fake_db: FakeTicketDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notified: list[str] = []
    monkeypatch.setattr(tickets, "_notify_student_if_bound", lambda t: notified.append(f"{t.id}:{t.status}:{t.reply}"))

    t = tickets.create_ticket("t1", "u1", "奖学金什么时候发放？", category="campus")
    with pytest.raises(ValueError, match="不能为空"):
        tickets.reply_ticket("t1", t.id, "staff-1", "   ", close=False)

    # 跨租户回复失败
    assert tickets.reply_ticket("t2", t.id, "staff-1", "无权回复") is None

    answered = tickets.reply_ticket("t1", t.id, "staff-1", "每年11月中旬打卡。")
    assert answered is not None
    assert answered.status == "answered"
    assert answered.replied_by_id == "staff-1"
    assert answered.replied_at is not None
    assert len(notified) == 1

    # 关单流程允许空回复沿用已有答复或直接关闭
    closed = tickets.reply_ticket("t1", t.id, "staff-1", "", close=True)
    assert closed is not None and closed.status == "closed"


def test_merge_ticket_to_kb_writes_markdown_and_preserves_merged_status(
    fake_db: FakeTicketDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notified: list[str] = []
    monkeypatch.setattr(tickets, "_notify_student_if_bound", lambda t: notified.append(t.id))

    t = tickets.create_ticket("t1", "u1", "辅修报名截止时间是哪天？", category="academic")
    fake_kb = MagicMock()
    fake_kb.add_upload.return_value = SimpleNamespace(name=f"工单答疑_{t.id[:8]}.md")

    # 既没回复又没传入 reply_override 时拒绝直接入库
    with pytest.raises(ValueError, match="先填写解答内容"):
        tickets.merge_ticket_to_kb("t1", t.id, "staff-1", kb=fake_kb)

    merged, doc = tickets.merge_ticket_to_kb(
        "t1",
        t.id,
        "staff-1",
        reply_override="每年春季学期第2周周五17:00截止在教务系统提交。",
        title="辅修报名截止时间说明",
        kb=fake_kb,
    )
    assert merged is not None
    assert merged.status == "merged"
    assert merged.merged_at is not None
    assert doc.name == f"工单答疑_{t.id[:8]}.md"
    fake_kb.add_upload.assert_called_once()
    uploaded_name, uploaded_bytes, uploaded_tenant = fake_kb.add_upload.call_args.args
    assert uploaded_name == doc.name
    assert uploaded_tenant == "t1"
    md_text = uploaded_bytes.decode("utf-8")
    assert "# 问：辅修报名截止时间说明" in md_text
    assert "## 学生原问题" in md_text and "辅修报名截止时间是哪天？" in md_text
    assert "## 官方核准解答" in md_text and "春季学期第2周" in md_text
    fake_kb._schedule_index.assert_called_once_with("t1")
    assert notified == [t.id]

    # 已回流的工单二次修订回复，仍保留 merged 状态
    revised = tickets.reply_ticket("t1", t.id, "staff-2", "补充：需先完成主修大类分流。")
    assert revised is not None and revised.status == "merged"
