"""pet_sync 与 term_review 的单元测试。"""
from __future__ import annotations

import time
from datetime import date
from typing import Any

import jw_account
import pet_sync
import pytest
import term_review
import today_card
from workspace import SchoolAi

MONDAY = date(2026, 10, 12)  # 演示模式第 7 周


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expiry: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        if ex:
            self.expiry[key] = ex

    def delete(self, *keys: str) -> None:
        for k in keys:
            self.data.pop(k, None)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(pet_sync, "_redis", lambda: fake)
    monkeypatch.setattr(today_card, "_redis", lambda: fake)
    return fake


def _setup_demo(monkeypatch: pytest.MonkeyPatch, mode: str = "demo") -> None:
    school = SchoolAi(jw_mode=mode)
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: school)
    monkeypatch.setattr(today_card, "school_ai", lambda tenant_id: school)
    monkeypatch.setattr(term_review, "school_ai", lambda tenant_id: school)


def test_pet_token_lifecycle(fake_redis: FakeRedis) -> None:
    # 1. 初始状态
    assert pet_sync.pet_token_status("t1", "u1") == {"active": False}

    # 2. 创建令牌
    token = pet_sync.create_pet_token("t1", "u1")
    assert len(token) > 20
    status = pet_sync.pet_token_status("t1", "u1")
    assert status["active"] is True

    # 3. 再次创建自动覆盖旧令牌
    token2 = pet_sync.create_pet_token("t1", "u1")
    assert token2 != token
    assert pet_sync.feed_pet_by_token(token, MONDAY) is None

    # 4. 撤销
    pet_sync.revoke_pet_token("t1", "u1")
    assert pet_sync.pet_token_status("t1", "u1") == {"active": False}


def test_pet_feed_data_and_bubbles(fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_demo(monkeypatch)
    token = pet_sync.create_pet_token("t1", "u1")

    # 早晨 07:30，两节课都还没上
    feed_morning = pet_sync.feed_pet_by_token(token, MONDAY, now_clock="07:30")
    assert feed_morning is not None
    assert feed_morning["bound"] is True
    assert feed_morning["total_lessons_today"] == 2
    assert feed_morning["lessons_left"] == 2
    assert feed_morning["next_lesson"]["course"] == "高等数学（演示）"
    assert any("今天还有 2 节课" in line for line in feed_morning["bubble_lines"])

    # 中午 12:30，两节课上完了 (Demo课表是 08:00-09:40, 10:00-11:40)
    feed_noon = pet_sync.feed_pet_by_token(token, MONDAY, now_clock="12:30")
    assert feed_noon["lessons_left"] == 0
    assert any("全都上完啦" in line for line in feed_noon["bubble_lines"])


def test_term_review_calculation(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_demo(monkeypatch)

    review = term_review.build_term_review("t1", "u1", MONDAY)
    assert review["bound"] is True
    assert review["current_week"] == 7
    assert review["total_courses"] == 6
    # 累计课时推算应该大于 0
    assert review["total_lessons_estimated"] > 0
    assert "星期" in review["busiest_day"]
    assert len(review["tags"]) > 0
    assert review["curr_gpa"] is not None
