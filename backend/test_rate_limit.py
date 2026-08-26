"""限流回归测试。用内存假 Redis 驱动，不需要真实服务:

    cd backend && pytest test_rate_limit.py
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from rate_limit import QuotaRule, consume, enforce_chat_quota


class FakeRedis:
    """只实现 incr / expire / ttl 三个用到的命令。"""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        self.expire_calls.append((key, seconds))
        return True

    def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)


RULE = QuotaRule(max_requests=3, window_seconds=60)


def test_allows_up_to_the_limit() -> None:
    fake = FakeRedis()
    assert [consume("u1", RULE, client=fake) for _ in range(3)] == [1, 2, 3]


def test_blocks_past_the_limit_with_429() -> None:
    fake = FakeRedis()
    for _ in range(3):
        consume("u1", RULE, client=fake)

    with pytest.raises(HTTPException) as exc:
        consume("u1", RULE, client=fake)

    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "60"


def test_ttl_is_set_once_so_window_does_not_slide_forever() -> None:
    """每次请求都刷新 TTL 的话，持续刷请求的人永远等不到窗口过期。"""
    fake = FakeRedis()
    for _ in range(3):
        consume("u1", RULE, client=fake)

    assert len(fake.expire_calls) == 1


def test_users_are_counted_separately() -> None:
    fake = FakeRedis()
    for _ in range(3):
        consume("u1", RULE, client=fake)

    # u1 已经用满，u2 不该受影响
    assert consume("u2", RULE, client=fake) == 1


def test_chat_quota_key_is_namespaced_per_user() -> None:
    fake = FakeRedis()
    enforce_chat_quota("user-1", client=fake)
    enforce_chat_quota("user-2", client=fake)

    assert set(fake.counts) == {"ratelimit:chat:user-1", "ratelimit:chat:user-2"}


def test_missing_ttl_falls_back_to_window_length() -> None:
    """key 在计数与读 TTL 之间过期时，Retry-After 不能是 -1。"""
    fake = FakeRedis()
    for _ in range(4):
        fake.incr("ratelimit:u1")  # 直接造出超限但没有 TTL 的状态

    with pytest.raises(HTTPException) as exc:
        consume("u1", RULE, client=fake)

    assert exc.value.headers["Retry-After"] == "60"
