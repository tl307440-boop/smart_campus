"""长期记忆存储回归测试。用进程内的假 Redis，不需要真实服务:
    cd backend && pytest test_memory_store.py
"""
from __future__ import annotations

import memory_store
import pytest


class FakeRedis:
    """只实现 memory_store 用到的 hash 命令。"""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, str]] = {}

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.data.get(key, {}))

    def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.data.setdefault(key, {}).update(mapping)
        return len(mapping)

    def hdel(self, key: str, *fields: str) -> int:
        bucket = self.data.get(key, {})
        return sum(1 for field in fields if bucket.pop(field, None) is not None)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    # 连续写入可能落在同一个时钟刻度上，排序就不确定了
    ticks = iter(range(1_000_000, 2_000_000))
    monkeypatch.setattr(memory_store.time, "time", lambda: float(next(ticks)))
    return FakeRedis()


def test_add_dedupes_and_lists_newest_first(fake: FakeRedis) -> None:
    memory_store.add_memories("t1", "u1", ["我是计算机学院的"], client=fake)
    added = memory_store.add_memories("t1", "u1", ["我是计算机学院的", "我读大三"], client=fake)

    assert [note.content for note in added] == ["我读大三"]
    notes = memory_store.list_memories("t1", "u1", client=fake)
    assert [note.content for note in notes] == ["我读大三", "我是计算机学院的"]


def test_users_are_isolated(fake: FakeRedis) -> None:
    """同校不同人、同 id 不同校，都不能读到对方的记忆。"""
    memory_store.add_memories("t1", "u1", ["甲的秘密"], client=fake)

    assert memory_store.list_memories("t1", "u2", client=fake) == []
    assert memory_store.list_memories("t2", "u1", client=fake) == []


def test_capacity_drops_oldest(fake: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_store, "MAX_MEMORIES", 3)
    memory_store.add_memories("t1", "u1", ["一", "二"], client=fake)
    memory_store.add_memories("t1", "u1", ["三", "四"], client=fake)

    contents = {note.content for note in memory_store.list_memories("t1", "u1", client=fake)}
    assert contents == {"二", "三", "四"}


def test_content_is_trimmed_and_capped(fake: FakeRedis) -> None:
    long_text = "很长" * 300
    added = memory_store.add_memories("t1", "u1", ["  有   空格  ", long_text, "   "], client=fake)

    assert added[0].content == "有 空格"
    assert len(added[1].content) == memory_store.MAX_MEMORY_CHARS
    assert len(added) == 2


def test_delete(fake: FakeRedis) -> None:
    note = memory_store.add_memories("t1", "u1", ["要删的"], client=fake)[0]

    assert memory_store.delete_memory("t1", "u1", note.id, client=fake) is True
    assert memory_store.delete_memory("t1", "u1", note.id, client=fake) is False
    assert memory_store.list_memories("t1", "u1", client=fake) == []


def test_relevant_memories_prefers_overlap(fake: FakeRedis) -> None:
    """中文按两字切词：问「奖学金」时应先拿到与奖学金相关的那条。"""
    memory_store.add_memories(
        "t1", "u1", ["我拿过国家奖学金", "我住在三号宿舍", "我喜欢打篮球"], client=fake
    )

    picked = memory_store.relevant_memories("t1", "u1", "今年奖学金怎么评", limit=1, client=fake)
    assert picked == ["我拿过国家奖学金"]


def test_relevant_memories_falls_back_to_recent(fake: FakeRedis) -> None:
    memory_store.add_memories("t1", "u1", ["旧的"], client=fake)
    memory_store.add_memories("t1", "u1", ["新的"], client=fake)

    assert memory_store.relevant_memories("t1", "u1", "完全无关", limit=1, client=fake) == ["新的"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("记住：我是计算机学院的", "我是计算机学院的"),
        ("记住:  我读大三 ", "我读大三"),
        ("请记住这件事", None),
        ("记住：   ", None),
    ],
)
def test_remember_command(text: str, expected: str | None) -> None:
    assert memory_store.take_remember_command(text) == expected
