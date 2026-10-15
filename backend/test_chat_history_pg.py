"""chat_history 的手写 SQL 对真库跑一遍：建一个临时 schema，套上 Prisma 迁移，用完删掉。

需要一个能建 schema 的 Postgres，没配就跳过:
    CHAT_HISTORY_TEST_DATABASE_URL=postgresql://campus:testpw@localhost:55433/smart_campus pytest test_chat_history_pg.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import chat_history
import psycopg
import pytest

TEST_URL = os.getenv("CHAT_HISTORY_TEST_DATABASE_URL", "").strip()
MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "frontend" / "prisma" / "migrations" / "20261015090000_chat_history" / "migration.sql"
)

pytestmark = pytest.mark.skipif(not TEST_URL, reason="没配 CHAT_HISTORY_TEST_DATABASE_URL")

TENANT = "tenant-1"
ALICE = "user-alice"
BOB = "user-bob"


@pytest.fixture(autouse=True)
def database(monkeypatch: pytest.MonkeyPatch):
    schema = f"chat_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(TEST_URL, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    joiner = "&" if "?" in TEST_URL else "?"
    url = f"{TEST_URL}{joiner}options=-csearch_path%3D{schema}"
    with psycopg.connect(url, autocommit=True) as conn:
        # 迁移只依赖 tenants / users 的主键，这里只建最小的两张表
        conn.execute('CREATE TABLE tenants (id TEXT PRIMARY KEY)')
        conn.execute('CREATE TABLE users (id TEXT PRIMARY KEY, "tenantId" TEXT NOT NULL REFERENCES tenants(id))')
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO tenants VALUES (%s)", (TENANT,))
        conn.execute("INSERT INTO users VALUES (%s, %s), (%s, %s)", (ALICE, TENANT, BOB, TENANT))
    monkeypatch.setenv("DATABASE_URL", url)
    yield url
    with psycopg.connect(TEST_URL, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _turn(question: str, answer: str, **extra) -> tuple[dict, dict]:
    return {"role": "user", "content": question}, {"role": "assistant", "content": answer, **extra}


def test_turns_get_consecutive_seq_and_title_from_first_question() -> None:
    table = {"title": "空教室", "columns": [{"key": "room", "label": "教室"}], "rows": [{"room": "3-105"}], "total": 1}
    assert chat_history.record_turn(TENANT, ALICE, "s1", *_turn("  三教  现在有空教室吗 ", "有一间", tables=[table])) == 2
    assert chat_history.record_turn(TENANT, ALICE, "s1", *_turn("那四教呢", "没有")) == 4

    messages = chat_history.load_messages(TENANT, ALICE, "s1")
    assert [m["content"] for m in messages] == ["  三教  现在有空教室吗 ", "有一间", "那四教呢", "没有"]
    assert messages[1]["tables"] == [table]
    assert chat_history.list_sessions(TENANT, ALICE)[0].title == "三教 现在有空教室吗"


def test_resubmitting_the_same_turn_is_ignored() -> None:
    chat_history.record_turn(TENANT, ALICE, "s1", *_turn("在吗", "在"))
    assert chat_history.record_turn(TENANT, ALICE, "s1", *_turn("在吗", "在")) is None
    assert len(chat_history.load_messages(TENANT, ALICE, "s1")) == 2


def test_someone_elses_thread_id_is_never_written_or_read() -> None:
    chat_history.record_turn(TENANT, ALICE, "s1", *_turn("我的学号是多少", "20260001"))

    assert chat_history.record_turn(TENANT, BOB, "s1", *_turn("偷看", "不行")) is None
    assert chat_history.load_messages(TENANT, BOB, "s1") is None
    assert chat_history.recent_messages(TENANT, BOB, "s1", 10) == []
    assert chat_history.list_sessions(TENANT, BOB) == []
    assert chat_history.delete_session(TENANT, BOB, "s1") is False
    assert len(chat_history.load_messages(TENANT, ALICE, "s1")) == 2


def test_foreign_thread_id_is_blocked_for_the_intruder_only() -> None:
    chat_history.record_turn(TENANT, ALICE, "s1", *_turn("在吗", "在"))
    assert chat_history.is_blocked(TENANT, BOB, "s1")
    assert not chat_history.is_blocked(TENANT, ALICE, "s1")
    assert not chat_history.is_blocked(TENANT, BOB, "brand-new")


def test_drop_last_turn_only_when_question_matches() -> None:
    chat_history.record_turn(TENANT, ALICE, "s1", *_turn("在吗", "在"))
    chat_history.record_turn(TENANT, ALICE, "s1", *_turn("几点关门", "十点"))

    assert chat_history.drop_last_turn(TENANT, ALICE, "s1", "在吗") is False
    assert chat_history.drop_last_turn(TENANT, BOB, "s1", "几点关门") is False
    assert chat_history.drop_last_turn(TENANT, ALICE, "s1", "几点关门") is True
    assert [m["content"] for m in chat_history.load_messages(TENANT, ALICE, "s1")] == ["在吗", "在"]
    assert chat_history.record_turn(TENANT, ALICE, "s1", *_turn("几点关门", "十点半")) == 4


def test_list_is_newest_first() -> None:
    chat_history.record_turn(TENANT, ALICE, "old", *_turn("第一个", "好"))
    chat_history.record_turn(TENANT, ALICE, "new", *_turn("第二个", "好"))
    chat_history.record_turn(TENANT, ALICE, "old", *_turn("又回来了", "好"))

    assert [s.thread_id for s in chat_history.list_sessions(TENANT, ALICE)] == ["old", "new"]
    assert len(chat_history.list_sessions(TENANT, ALICE, limit=1)) == 1


def test_deleted_session_stays_deleted() -> None:
    chat_history.record_turn(TENANT, ALICE, "s1", *_turn("在吗", "在"))
    assert chat_history.delete_session(TENANT, ALICE, "s1") is True

    assert chat_history.is_blocked(TENANT, ALICE, "s1")
    assert chat_history.load_messages(TENANT, ALICE, "s1") is None
    assert chat_history.record_turn(TENANT, ALICE, "s1", *_turn("还在吗", "在")) is None
    assert chat_history.backfill(TENANT, ALICE, "s1", list(_turn("在吗", "在"))) is False
    assert not chat_history.is_blocked(TENANT, BOB, "s1")


def test_backfill_only_fills_an_empty_session() -> None:
    first = list(_turn("门禁几点", "十一点")) + list(_turn("周末呢", "一样"))
    assert chat_history.backfill(TENANT, ALICE, "s1", first) is True
    assert chat_history.backfill(TENANT, ALICE, "s1", first) is False
    assert [m["content"] for m in chat_history.load_messages(TENANT, ALICE, "s1")] == ["门禁几点", "十一点", "周末呢", "一样"]
    assert chat_history.list_sessions(TENANT, ALICE)[0].title == "门禁几点"


def test_recent_messages_never_start_with_an_orphan_answer() -> None:
    for index in range(3):
        chat_history.record_turn(TENANT, ALICE, "s1", *_turn(f"问{index}", f"答{index}"))

    assert [m["content"] for m in chat_history.recent_messages(TENANT, ALICE, "s1", 3)] == ["问2", "答2"]
    assert [m["content"] for m in chat_history.recent_messages(TENANT, ALICE, "s1", 4)] == ["问1", "答1", "问2", "答2"]


def test_summary_job_waits_for_a_full_batch_and_saves_once() -> None:
    for index in range(6):
        chat_history.record_turn(TENANT, ALICE, "s1", *_turn(f"问{index}", f"答{index}"))
    # 6 轮，留最近 2 轮，可压 4 轮，不够 5 轮一批
    assert chat_history.summary_job(TENANT, ALICE, "s1", keep_turns=2, batch_turns=5) is None

    job = chat_history.summary_job(TENANT, ALICE, "s1", keep_turns=2, batch_turns=4)
    assert (job.from_seq, job.upto_seq) == (0, 8)
    assert [m["content"] for m in job.messages][-1] == "答3"
    assert chat_history.summary_job(TENANT, BOB, "s1", keep_turns=2, batch_turns=4) is None

    assert chat_history.save_summary(TENANT, ALICE, "s1", job, "学生在找空教室") is True
    # 同一个任务第二次写入要作废，免得两个后台线程互相覆盖
    assert chat_history.save_summary(TENANT, ALICE, "s1", job, "旧的") is False
    assert chat_history.summary(TENANT, ALICE, "s1") == "学生在找空教室"
    assert chat_history.summary(TENANT, BOB, "s1") == ""
    assert chat_history.summary_job(TENANT, ALICE, "s1", keep_turns=2, batch_turns=4) is None


def test_deleting_the_user_cascades() -> None:
    chat_history.record_turn(TENANT, ALICE, "s1", *_turn("在吗", "在"))
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (ALICE,))
        assert conn.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 0
