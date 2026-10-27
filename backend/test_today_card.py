"""今日卡片：今天的课按周次筛、临近考试只列窗口内的、按人按天缓存、没绑定时只给状态。"""
from __future__ import annotations

import time
from datetime import date
from typing import Any

import api
import auth
import jw_account
import jwt
import pytest
import today_card
from auth import JWT_ALGORITHM, JWT_ISSUER
from fastapi.testclient import TestClient
from jw_zhengfang import JwAuthError, JwError
from workspace import EmbedSettings, SchoolAi

SECRET = "test-secret-do-not-use-in-production"
EMBED = EmbedSettings(id="embed-1", tenant_id="tenant-1", daily_limit=50, enabled=True)
MONDAY = date(2026, 10, 12)  # 演示课表是第 7 周


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


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(today_card, "_redis", lambda: fake)
    return fake


def _demo(monkeypatch: pytest.MonkeyPatch, mode: str = "demo") -> None:
    school = SchoolAi(jw_mode=mode)  # type: ignore[arg-type]
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: school)
    monkeypatch.setattr(today_card, "school_ai", lambda tenant_id: school)


def test_card_lists_todays_lessons_with_times(cache: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)

    card = today_card.build_card("t1", "u1", MONDAY)

    assert (card["enabled"], card["bound"], card["weekday"], card["week"]) == (True, True, "星期一", 7)
    assert [(c["course"], c["start"], c["end"]) for c in card["lessons"]] == [
        ("高等数学（演示）", "08:00", "09:40"),
        ("大学英语（演示）", "10:00", "11:40"),
    ]
    assert set(card["lessons"][0]) == {"course", "periods", "start", "end", "room", "teacher"}
    assert card["week_unknown"] is False


def test_lessons_follow_the_week(cache: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)
    monkeypatch.setattr(jw_account.DemoJw, "current_week", lambda self: 7)
    # 线性代数只在双周的周五上：第 7 周没有，第 8 周有
    friday = date(2026, 10, 16)
    assert today_card.build_card("t1", "u1", friday)["lessons"] == []
    monkeypatch.setattr(jw_account.DemoJw, "current_week", lambda self: 8)
    assert [c["course"] for c in today_card.build_card("t1", "u2", friday)["lessons"]] == ["线性代数（演示）"]


def test_unknown_week_is_flagged_and_not_filtered(cache: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)
    monkeypatch.setattr(jw_account.DemoJw, "current_week", lambda self: None)

    card = today_card.build_card("t1", "u1", MONDAY)

    assert card["week"] is None and card["week_unknown"] is True
    assert len(card["lessons"]) == 2


def test_school_without_jw_is_disabled(cache: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch, "off")
    assert today_card.build_card("t1", "u1", MONDAY) == {"enabled": False, "bound": False}


def test_unbound_student_gets_status_only_and_is_not_cached(cache: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch, "zhengfang")

    def not_bound(tenant_id: str, user_id: str) -> Any:
        raise JwAuthError("你还没有绑定教务账号")

    monkeypatch.setattr(jw_account, "source_for", not_bound)

    assert today_card.build_card("t1", "u1", MONDAY) == {"enabled": True, "bound": False}
    assert cache.data == {}  # 刚绑定完再打开页面要立刻看到课


def test_card_is_cached_per_user_and_only_for_its_own_day(cache: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)
    calls = []
    original = jw_account.DemoJw.timetable
    monkeypatch.setattr(jw_account.DemoJw, "timetable", lambda self, *a: calls.append(1) or original(self, *a))

    first = today_card.build_card("t1", "u1", MONDAY)
    again = today_card.build_card("t1", "u1", MONDAY)
    other_user = today_card.build_card("t1", "u2", MONDAY)
    next_day = today_card.build_card("t1", "u1", date(2026, 10, 13))  # 跨了天：昨天缓存的不能拿来用

    assert first == again and len(calls) == 3  # u1 第二次走缓存，u2 和第二天各查一次
    assert other_user["lessons"] == first["lessons"] and next_day["lessons"] != first["lessons"]
    assert next_day["date"] == "2026-10-13"
    assert set(cache.expiry.values()) == {today_card.CACHE_SECONDS}
    assert set(cache.data) == {jw_account.today_card_key("t1", "u1"), jw_account.today_card_key("t1", "u2")}


def test_jw_outage_is_not_cached(cache: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)

    def down(self: Any, *args: Any) -> Any:
        raise JwError("教务系统暂时打不开")

    monkeypatch.setattr(jw_account.DemoJw, "timetable", down)

    with pytest.raises(JwError):
        today_card.build_card("t1", "u1", MONDAY)
    assert cache.data == {}


def _exam(time_text: str, course: str = "高等数学") -> dict[str, str]:
    return {"course": course, "time": time_text, "room": "A101", "campus": "", "name": "期末考试", "seat": "12"}


def test_only_near_exams_are_listed_nearest_first() -> None:
    exams = [
        _exam("2026-10-30(08:00-10:00)", "远一点"),
        _exam("2026-10-12(14:00-16:00)", "今天"),
        _exam("2026-10-10(09:00-11:00)", "已经考完"),
        _exam("2027-01-08(09:00-11:00)", "太远"),
        _exam("下周三上午", "读不出日期"),
        _exam("2026-13-40(09:00-11:00)", "日期不存在"),
        _exam("2026-10-20", "没写时段"),
    ]

    found = today_card.upcoming_exams(exams, MONDAY)

    assert [(e["course"], e["days_left"]) for e in found] == [("今天", 0), ("没写时段", 8), ("远一点", 18)]
    assert (found[0]["start"], found[0]["end"], found[0]["seat"]) == ("14:00", "16:00", "12")
    assert found[1]["start"] == "" and found[1]["end"] == ""


def test_at_most_three_exams() -> None:
    exams = [_exam(f"2026-10-{day}(09:00-11:00)", f"课{day}") for day in (13, 14, 15, 16, 17)]
    assert [e["course"] for e in today_card.upcoming_exams(exams, MONDAY)] == ["课13", "课14", "课15"]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (None, date(2026, 10, 12)),
        ("2026-10-13", date(2026, 10, 13)),  # 服务器还在昨天，学生已经到了第二天
        ("2026-10-11", date(2026, 10, 11)),
        ("2026-10-20", date(2026, 10, 12)),  # 差太多不采信
        ("not-a-date", date(2026, 10, 12)),
        ("2026-02-30", date(2026, 10, 12)),
    ],
)
def test_client_day_is_only_trusted_within_a_day(requested: str | None, expected: date) -> None:
    assert today_card.clamp_day(requested, MONDAY) == expected


# ---------- 接口 ----------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    monkeypatch.setattr(auth, "embed_settings", lambda eid: EMBED if eid == EMBED.id else None)
    return TestClient(api.app)


def _headers(role: str = "STUDENT") -> dict[str, str]:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": "user-1", "tid": "tenant-1", "sno": "20260001", "name": "张三",
        "role": role, "iss": JWT_ISSUER, "iat": now, "exp": now + 3600,
    }
    if role == "GUEST":
        claims.update(sub="guest-embed-1-v1", eid=EMBED.id)
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm=JWT_ALGORITHM)}"}


def test_route_passes_caller_and_client_day(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, date]] = []
    monkeypatch.setattr(today_card, "build_card", lambda tid, uid, day: seen.append((tid, uid, day)) or {"enabled": False})
    monkeypatch.setattr(today_card, "clamp_day", lambda requested, server: date.fromisoformat(requested) if requested else server)

    response = client.get("/api/today?day=2026-10-13", headers=_headers())

    assert response.status_code == 200 and response.json() == {"enabled": False}
    assert seen == [("tenant-1", "user-1", date(2026, 10, 13))]


def test_route_rejects_malformed_day(client: TestClient) -> None:
    assert client.get("/api/today?day=tomorrow", headers=_headers()).status_code == 422


def test_route_turns_jw_failures_into_readable_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(tid: str, uid: str, day: date) -> Any:
        raise JwError("教务系统暂时打不开")

    monkeypatch.setattr(today_card, "build_card", down)

    response = client.get("/api/today", headers=_headers())

    assert response.status_code == 502 and "教务" in response.json()["detail"]


def test_guest_cannot_read_the_card(client: TestClient) -> None:
    assert client.get("/api/today", headers=_headers("GUEST")).status_code == 403
    assert client.get("/api/today").status_code in (401, 403)
