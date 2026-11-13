"""IM 主动推送调度器单元测试（晨间早报、成绩异动追踪、工单通知与去重）。"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace


def _make_binding(**overrides) -> im_gateway.ImBinding:
    defaults = {
        "id": "b1",
        "tenant_id": "t1",
        "user_id": "u1",
        "platform": "qq",
        "external_id": "10001",
        "display_name": "小明",
        "morning_digest": True,
        "digest_time": "07:30",
        "grade_alert": True,
        "ticket_alert": True,
        "news_keywords": "",
        "last_grade_hash": "",
    }
    defaults.update(overrides)
    return im_gateway.ImBinding(**defaults)

import im_gateway
import im_scheduler
import pytest
import today_card
from workspace import SchoolAi


class DedupeFakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True


def test_morning_digest_combines_lessons_exams_news_and_flashcards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = date(2026, 11, 13)
    monkeypatch.setattr(
        today_card,
        "build_card",
        lambda tid, uid, d: {
            "enabled": True,
            "bound": True,
            "week": 11,
            "weekday": "星期五",
            "lessons": [{"start": "08:00", "end": "09:40", "course": "计算机网络", "room": "致远楼 302"}],
            "exams": [{"course": "线性代数", "date": "2026-11-14", "start": "14:00", "room": "博学楼 101", "days_left": 1}],
        },
    )
    monkeypatch.setattr(
        im_scheduler,
        "school_ai",
        lambda tid: SchoolAi(news_urls=["https://example.edu/news"]),
    )
    monkeypatch.setattr(
        im_scheduler.school_tools,
        "school_news",
        lambda urls, keyword="", limit=15: [
            SimpleNamespace(title="关于申报国家奖学金的通知", site="学生处", url="https://example.edu/1", date="11-12"),
            SimpleNamespace(title="食堂二楼暂停营业通知", site="后勤处", url="https://example.edu/2", date="11-12"),
        ],
    )
    monkeypatch.setattr(im_scheduler, "_due_flashcard_count", lambda tid, uid: 5)

    text = im_scheduler.build_morning_digest("t1", "u1", day, news_keywords="奖学金")
    assert "今日校园早报" in text
    assert "第 11 周" in text
    assert "计算机网络" in text and "致远楼 302" in text
    assert "【明天】线性代数" in text
    assert "匹配关键词：奖学金" in text
    assert "关于申报国家奖学金的通知" in text
    assert "食堂二楼" not in text
    assert "5" in text and "伴学空间" in text


def test_grades_hash_order_invariant_and_delta_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = DedupeFakeRedis()
    monkeypatch.setattr(im_scheduler, "_redis", lambda: fake_redis)
    im_scheduler._LOCAL_GRADES.clear()

    hashes: dict[str, str] = {}
    monkeypatch.setattr(im_gateway, "update_grade_hash", lambda bid, h: hashes.__setitem__(bid, h))

    g1 = {"term": "2026-1", "course": "高等数学", "score": "92", "credit": "5", "gpa": "4.2"}
    g2 = {"term": "2026-1", "course": "英语视听说", "score": "85", "credit": "2", "gpa": "3.5"}
    assert im_scheduler.compute_grades_hash([g1, g2]) == im_scheduler.compute_grades_hash([g2, g1])

    binding = _make_binding(id="b1", last_grade_hash="")
    # 首次同步建立基线，不应推送打扰
    assert im_scheduler.check_and_build_grade_alert(binding, [g1]) is None
    binding = replace(binding, last_grade_hash=hashes["b1"])

    # 无变化再次轮询，不推送
    assert im_scheduler.check_and_build_grade_alert(binding, [g1]) is None

    # 仅新增一门成绩，精准推送新增项与加权 GPA
    alert = im_scheduler.check_and_build_grade_alert(binding, [g1, g2])
    assert alert is not None
    assert "出分提醒" in alert
    assert "英语视听说" in alert and "85" in alert
    assert "高等数学" not in alert  # 未变动的旧课不刷屏
    assert "4.0" in alert  # (4.2*5 + 3.5*2) / 7 = 4.0


def test_notify_ticket_reply_respects_ticket_alert_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        im_gateway,
        "list_user_bindings",
        lambda tid, uid: [
            {"platform": "qq", "external_id": "10001", "ticket_alert": True},
            {"platform": "wecom", "external_id": "wx-u1", "ticket_alert": False},
        ],
    )
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        im_gateway,
        "send_im_message",
        lambda tid, plat, ext, txt: sent.append((plat, ext, txt)) or True,
    )

    ticket = SimpleNamespace(
        tenant_id="t1",
        user_id="u1",
        question="缓考要找谁盖章？",
        reply="请找学院教学秘书审核后交教务处。",
        status="merged",
    )
    count = im_scheduler.notify_ticket_reply(ticket)
    assert count == 1
    assert len(sent) == 1
    assert sent[0][0] == "qq" and "已回流学校知识库" in sent[0][2]


def test_run_once_dedupes_daily_digest_and_runs_grade_check(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = DedupeFakeRedis()
    monkeypatch.setattr(im_scheduler, "_redis", lambda: fake_redis)
    im_scheduler._LOCAL_DEDUPE.clear()
    im_scheduler._LOCAL_GRADES.clear()

    state = {"binding": _make_binding(id="b-qq", last_grade_hash="old-hash")}
    im_scheduler._write_prev_grade_map("t1", "u1", {"2026-1|高数|5": "90"})

    monkeypatch.setattr(im_gateway, "list_active_bindings", lambda: [state["binding"]])
    monkeypatch.setattr(im_gateway, "update_grade_hash", lambda bid, h: state.__setitem__("binding", replace(state["binding"], last_grade_hash=h)))
    monkeypatch.setattr(im_scheduler, "build_morning_digest", lambda t, u, d, kw="": "MOCK DIGEST")
    monkeypatch.setattr(im_scheduler, "school_ai", lambda tid: SchoolAi(jw_mode="demo"))

    class FakeJw:
        def grades(self, xnm: str, xqm: str):
            return [
                {"term": "2026-1", "course": "高数", "score": "90", "credit": "5", "gpa": "4.0"},
                {"term": "2026-1", "course": "线代", "score": "95", "credit": "3", "gpa": "4.5"},
            ]

    @contextmanager
    def fake_src(tid: str, uid: str):
        yield FakeJw()

    monkeypatch.setattr(im_scheduler.jw_account, "source_for", fake_src)

    delivered: list[str] = []
    monkeypatch.setattr(im_gateway, "send_im_message", lambda t, p, e, txt: delivered.append(txt) or True)

    summary1 = im_scheduler.run_once(now=datetime(2026, 11, 13, 7, 35))
    assert summary1 == {"digests_sent": 1, "grade_alerts_sent": 1}
    assert any("MOCK DIGEST" in d for d in delivered)
    assert any("线代" in d for d in delivered)

    # 同一天两分钟后再轮询一次：早报去重、成绩间隔内跳过，不得重复打扰
    summary2 = im_scheduler.run_once(now=datetime(2026, 11, 13, 7, 37))
    assert summary2 == {"digests_sent": 0, "grade_alerts_sent": 0}
