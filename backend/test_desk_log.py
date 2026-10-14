"""提问记录：写进 Redis、超长截断、写失败不影响调用方。"""
from __future__ import annotations

import json

import desk_log
import redis


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key: str, start: int, end: int) -> None:
        self.lists[key] = self.lists.get(key, [])[start : end + 1]


def test_question_is_stored_under_the_school(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr(desk_log, "_redis", lambda: fake)
    desk_log.remember_question("t1", "u1", "  帮我上网搜一下\n最新政策  ")
    desk_log.remember_question("t1", "", "   ")
    desk_log.remember_question("t1", "", "访客在问")
    saved = [json.loads(item) for item in fake.lists["desk:q:t1"]]
    assert [item["question"] for item in saved] == ["访客在问", "帮我上网搜一下\n最新政策"]
    assert saved[0]["user"] == "guest" and saved[1]["user"] == "u1" and saved[1]["cut"] is False


def test_long_question_is_cut(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr(desk_log, "_redis", lambda: fake)
    desk_log.remember_question("t1", "u1", "问" * (desk_log.QUESTION_CHARS + 20))
    item = json.loads(fake.lists["desk:q:t1"][0])
    assert item["cut"] is True and len(item["question"]) == desk_log.QUESTION_CHARS


def test_only_the_newest_questions_are_kept(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr(desk_log, "_redis", lambda: fake)
    for i in range(desk_log.QUESTION_KEEP + 5):
        desk_log.remember_question("t1", "u1", f"第{i}问")
    saved = [json.loads(item)["question"] for item in fake.lists["desk:q:t1"]]
    assert len(saved) == desk_log.QUESTION_KEEP
    assert saved[0] == f"第{desk_log.QUESTION_KEEP + 4}问"
    assert saved[-1] == "第5问"


def test_redis_outage_is_swallowed(monkeypatch) -> None:
    def down() -> FakeRedis:
        raise redis.ConnectionError("down")

    monkeypatch.setattr(desk_log, "_redis", down)
    desk_log.remember_question("t1", "u1", "还在吗")
