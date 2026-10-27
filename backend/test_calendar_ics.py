"""课表日历：.ics 内容、订阅令牌的生命周期、接口。"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any

import api
import auth
import calendar_ics
import jw_account
import jwt
import pytest
from auth import JWT_ALGORITHM, JWT_ISSUER
from calendar_ics import CalendarError, build_calendar
from fastapi.testclient import TestClient
from jw_account import DemoJw
from jw_zhengfang import JwAuthError
from workspace import EmbedSettings, SchoolAi

SECRET = "test-secret-do-not-use-in-production"
EMBED = EmbedSettings(id="embed-1", tenant_id="tenant-1", daily_limit=50, enabled=True)
WEEK1 = date(2026, 9, 7)  # 周一；演示数据里「今天」是第 7 周
TODAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    def delete(self, *keys: str) -> None:
        for key in keys:
            self.data.pop(key, None)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(calendar_ics, "_redis", lambda: fake)
    return fake


def _demo(monkeypatch: pytest.MonkeyPatch) -> None:
    school = SchoolAi(jw_mode="demo")  # type: ignore[arg-type]
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: school)


def _unfold(text: str) -> list[str]:
    return text.replace("\r\n ", "").split("\r\n")


def _build(exams: list[dict[str, str]] | None = None, alarm: int = 15) -> str:
    return build_calendar(DemoJw().timetable("2026", "3"), exams or [], WEEK1, alarm, now=NOW)[0]


# ---------- 内容 ----------


def test_every_week_of_a_course_becomes_one_event() -> None:
    text, stats = build_calendar(DemoJw().timetable("2026", "3"), [], WEEK1, now=NOW)
    # 高数 16 + 英语 16 + 程序设计 12 + 思政 8 + 体育 16 + 线代（2-16 双周）8
    assert stats == {"lessons": 76, "exams": 0, "skipped": 0}
    assert text.count("BEGIN:VEVENT") == 76


def test_times_and_weeks_land_on_the_right_dates() -> None:
    lines = _unfold(_build())
    # 高等数学：周一 1-2 节 08:00-09:40，第 1 周 = 开学那周的周一
    assert "DTSTART;TZID=Asia/Shanghai:20260907T080000" in lines
    assert "DTEND;TZID=Asia/Shanghai:20260907T094000" in lines
    # 线性代数：周五 1-2 节，只有双周；第 2 周的周五 = 9 月 18 日，第 1 周没有
    assert "DTSTART;TZID=Asia/Shanghai:20260918T080000" in lines
    assert "DTSTART;TZID=Asia/Shanghai:20260911T080000" not in lines


def test_lines_are_crlf_folded_and_never_split_a_character() -> None:
    text = _build()
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    for physical in text.split("\r\n"):
        assert len(physical.encode("utf-8")) <= 75
    # 折行再拼回去，汉字完整
    assert any(line.startswith("DESCRIPTION:第1周") for line in _unfold(text))
    text.encode("utf-8").decode("utf-8")


def test_text_is_escaped() -> None:
    data = {"courses": [{"course": "数学, 分析; 一\\二", "weekday": 1, "periods": "1-2节", "weeks": "1周", "room": "A楼\n201", "teacher": ""}],
            "periods": [{"period": "1", "start": "08:00", "end": "08:45"}, {"period": "2", "start": "08:55", "end": "09:40"}]}
    lines = _unfold(build_calendar(data, [], WEEK1, now=NOW)[0])
    assert "SUMMARY:数学\\, 分析\\; 一\\\\二" in lines
    assert "LOCATION:A楼\\n201" in lines


def test_uids_are_stable_so_reimporting_does_not_duplicate() -> None:
    first = [line for line in _unfold(_build()) if line.startswith("UID:")]
    assert first == [line for line in _unfold(_build()) if line.startswith("UID:")]
    assert len(set(first)) == len(first)


def test_alarm_minutes_and_none() -> None:
    assert "TRIGGER:-PT15M" in _build(alarm=15)
    assert "TRIGGER:-PT30M" in _build(alarm=30)
    assert "VALARM" not in _build(alarm=0)


def test_courses_without_a_readable_time_are_skipped_not_guessed() -> None:
    data = {"courses": [{"course": "没时间的课", "weekday": 2, "periods": "9-10节", "weeks": "1-4周", "room": "", "teacher": ""}], "periods": []}
    text, stats = build_calendar(data, [], WEEK1, now=NOW)
    assert stats["skipped"] == 1 and "BEGIN:VEVENT" not in text


def test_exams_with_and_without_a_time_slot() -> None:
    exams = [
        {"course": "高等数学", "name": "期末考试", "time": "2027-01-08(09:00-11:00)", "room": "1号楼 101", "seat": "12"},
        {"course": "英语", "name": "", "time": "2027-01-10", "room": "", "seat": ""},
        {"course": "坏数据", "name": "", "time": "待定", "room": "", "seat": ""},
    ]
    text, stats = build_calendar({"courses": [], "periods": []}, exams, WEEK1, now=NOW)
    lines = _unfold(text)
    assert stats["exams"] == 2
    assert "DTSTART;TZID=Asia/Shanghai:20270108T090000" in lines
    assert "LOCATION:1号楼 101 座位 12" in lines
    assert "DTSTART;VALUE=DATE:20270110" in lines and "DTEND;VALUE=DATE:20270111" in lines
    assert "TRIGGER:-P1D" in lines


# ---------- 从教务生成 ----------


def test_render_derives_week_one_from_the_current_week(monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)
    # 演示教务固定「现在第 7 周」：10-12 是周一，所以第 1 周的周一是 8-31
    text, stats = calendar_ics.render("t", "u", date(2026, 10, 12))
    assert "DTSTART;TZID=Asia/Shanghai:20260831T080000" in _unfold(text)
    assert stats["lessons"] == 76 and stats["exams"] == 1


def test_render_needs_a_term_start_when_the_week_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)
    monkeypatch.setattr(DemoJw, "current_week", lambda self: None)
    with pytest.raises(CalendarError, match="第一周的周一"):
        calendar_ics.render("t", "u", date(2026, 8, 20))
    text, _ = calendar_ics.render("t", "u", date(2026, 8, 20), week1_monday=date(2026, 8, 31))
    assert "20260831T080000" in text


def test_term_start_must_be_a_monday(monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)
    with pytest.raises(CalendarError, match="星期一"):
        calendar_ics.render("t", "u", date(2026, 10, 12), week1_monday=date(2026, 9, 1))


def test_unknown_alarm_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)
    assert "TRIGGER:-PT15M" in calendar_ics.render("t", "u", date(2026, 10, 12), alarm_minutes=7)[0]


def test_empty_timetable_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)
    monkeypatch.setattr(DemoJw, "timetable", lambda self, xnm, xqm: {"courses": [], "periods": []})
    with pytest.raises(CalendarError, match="还没有你的课表"):
        calendar_ics.render("t", "u", date(2026, 10, 12))


# ---------- 订阅令牌 ----------


def test_token_is_stored_hashed_and_shown_once(store: FakeRedis) -> None:
    token = calendar_ics.create_subscription("t", "u", 30)
    assert len(token) >= 40
    assert all(token not in key and token not in value for key, value in store.data.items())
    assert calendar_ics.subscription_status("t", "u")["alarm"] == 30


def test_regenerating_invalidates_the_old_link(store: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar_ics, "render", lambda *a, **k: ("ICS", {}))
    old = calendar_ics.create_subscription("t", "u")
    assert calendar_ics.feed(old, TODAY) == "ICS"
    new = calendar_ics.create_subscription("t", "u")
    assert calendar_ics.feed(old, TODAY) is None
    assert calendar_ics.feed(new, TODAY) == "ICS"


def test_revoke_closes_the_link_and_clears_the_cache(store: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar_ics, "render", lambda *a, **k: ("ICS", {}))
    token = calendar_ics.create_subscription("t", "u")
    calendar_ics.feed(token, TODAY)
    calendar_ics.revoke_subscription("t", "u")
    assert calendar_ics.feed(token, TODAY) is None
    assert calendar_ics.subscription_status("t", "u") == {"active": False}
    assert store.data == {}


def test_feed_is_cached_so_phones_do_not_hammer_the_jw_system(store: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(calendar_ics, "render", lambda *a, **k: calls.append(1) or ("ICS", {}))
    token = calendar_ics.create_subscription("t", "u")
    for _ in range(5):
        assert calendar_ics.feed(token, TODAY) == "ICS"
    assert len(calls) == 1


def test_feed_renders_for_the_token_owner_with_their_alarm(store: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(calendar_ics, "render", lambda *a, **k: seen.append(a) or ("ICS", {}))
    token = calendar_ics.create_subscription("tenant-9", "user-9", 5)
    calendar_ics.feed(token, TODAY)
    assert seen == [("tenant-9", "user-9", TODAY, 5)]


def test_unknown_token_is_none(store: FakeRedis) -> None:
    assert calendar_ics.feed("x" * 43, TODAY) is None


# ---------- 接口 ----------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, store: FakeRedis) -> TestClient:
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    monkeypatch.setattr(auth, "embed_settings", lambda eid: EMBED if eid == EMBED.id else None)
    return TestClient(api.app)


def _headers(role: str = "STUDENT", user: str = "user-1") -> dict[str, str]:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": user, "tid": "tenant-1", "sno": "20260001", "name": "张三",
        "role": role, "iss": JWT_ISSUER, "iat": now, "exp": now + 3600,
    }
    if role == "GUEST":
        claims.update(sub="guest-embed-1-v1", eid=EMBED.id)
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm=JWT_ALGORITHM)}"}


def test_export_downloads_an_ics_file(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _demo(monkeypatch)
    monkeypatch.setattr(calendar_ics, "current_term", lambda today: ("2026", "3"))
    monkeypatch.setattr(api, "date", type("D", (date,), {"today": classmethod(lambda cls: date(2026, 10, 12))}))
    response = client.get("/api/calendar/export?alarm=10", headers=_headers())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert "attachment" in response.headers["content-disposition"] and "UTF-8''" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert "TRIGGER:-PT10M" in response.text


def test_export_explains_failures(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def unbound(*args: Any, **kwargs: Any) -> Any:
        raise JwAuthError("你还没有绑定教务账号")

    monkeypatch.setattr(calendar_ics, "render", unbound)
    assert client.get("/api/calendar/export", headers=_headers()).status_code == 400

    def need_start(*args: Any, **kwargs: Any) -> Any:
        raise CalendarError("请填一下开学第一周的周一日期")

    monkeypatch.setattr(calendar_ics, "render", need_start)
    response = client.get("/api/calendar/export", headers=_headers())
    assert response.status_code == 400 and "周一" in response.json()["detail"]


def test_export_rejects_malformed_term_start(client: TestClient) -> None:
    assert client.get("/api/calendar/export?term_start=tomorrow", headers=_headers()).status_code == 422


def test_guests_cannot_export_or_subscribe(client: TestClient) -> None:
    assert client.get("/api/calendar/export", headers=_headers("GUEST")).status_code in (401, 403)
    assert client.post("/api/calendar/subscription", json={}, headers=_headers("GUEST")).status_code in (401, 403)
    assert client.get("/api/calendar/export").status_code in (401, 403)


def test_subscription_lifecycle_over_http(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar_ics, "render", lambda *a, **k: ("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", {}))
    assert client.get("/api/calendar/subscription", headers=_headers()).json() == {"active": False}

    created = client.post("/api/calendar/subscription", json={"alarm": 30}, headers=_headers()).json()
    assert created["path"] == f"/api/calendar/feed/{created['token']}.ics"
    status = client.get("/api/calendar/subscription", headers=_headers()).json()
    assert status["active"] and status["alarm"] == 30 and "token" not in status

    # 日历软件不带登录信息来取
    feed = client.get(created["path"])
    assert feed.status_code == 200 and feed.headers["content-type"].startswith("text/calendar")

    # 别人看不到、也关不掉我的订阅状态
    assert client.get("/api/calendar/subscription", headers=_headers(user="user-2")).json() == {"active": False}

    assert client.delete("/api/calendar/subscription", headers=_headers()).status_code == 204
    assert client.get(created["path"]).status_code == 404


def test_feed_rejects_junk_tokens(client: TestClient) -> None:
    assert client.get("/api/calendar/feed/" + "a" * 43 + ".ics").status_code == 404
    assert client.get("/api/calendar/feed/short.ics").status_code == 422
    assert client.get("/api/calendar/feed/" + "a" * 20 + "%20x.ics").status_code in (404, 422)


def test_subscription_rejects_unknown_alarm(client: TestClient) -> None:
    assert client.post("/api/calendar/subscription", json={"alarm": 7}, headers=_headers()).status_code == 422


def test_feed_unbound_owner_gets_a_readable_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    token = calendar_ics.create_subscription("tenant-1", "user-1")

    def unbound(*args: Any, **kwargs: Any) -> Any:
        raise JwAuthError("你还没有绑定教务账号")

    monkeypatch.setattr(calendar_ics, "render", unbound)
    response = client.get(f"/api/calendar/feed/{token}.ics")
    assert response.status_code == 400
