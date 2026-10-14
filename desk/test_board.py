"""在线、提问、操作三页的纯数据。不连数据库，也不连 Redis。"""
from __future__ import annotations

from datetime import datetime, timezone

import board

NOW = 1_800_000_000.0


def user(uid: str = "u1", **extra: object) -> dict:
    row = {
        "id": uid,
        "studentNo": "20260001",
        "name": "张三",
        "role": "STUDENT",
        "disabled": False,
        "lastLoginAt": datetime.fromtimestamp(NOW - 30, timezone.utc),
    }
    row.update(extra)
    return row


def test_recent_login_counts_as_online() -> None:
    page = board.build([user()], [], [], NOW)
    assert page["online"] == [{
        "name": "张三", "studentNo": "20260001", "role": "学生", "what": "登录了", "ago": "刚刚",
    }]


def test_quiet_and_disabled_people_are_not_online() -> None:
    quiet = user("old", lastLoginAt=datetime.fromtimestamp(NOW - 301, timezone.utc), name="安静")
    stopped = user("off", disabled=True, name="停用")
    page = board.build([quiet, stopped], [], [], NOW)
    assert page["online"] == []


def test_a_newer_question_replaces_the_login_line() -> None:
    page = board.build(
        [user()],
        [{"ts": NOW - 10, "user": "u1", "question": "开门了吗", "cut": False}],
        [],
        NOW,
    )
    assert page["online"][0]["what"] == "提问"
    assert page["questions"][0]["text"] == "开门了吗"


def test_old_questions_stay_on_the_list_but_not_online() -> None:
    page = board.build(
        [user(lastLoginAt=None)],
        [{"ts": NOW - 400, "user": "u1", "question": "昨天的问题", "cut": False}],
        [],
        NOW,
    )
    assert page["online"] == []
    assert page["questions"][0]["time"]


def test_guest_and_deleted_accounts_keep_their_labels() -> None:
    page = board.build(
        [],
        [
            {"ts": NOW - 5, "user": "guest", "question": "访客在问", "cut": False},
            {"ts": NOW - 6, "user": "gone", "question": "旧账号的问题", "cut": True},
        ],
        [],
        NOW,
    )
    assert page["online"][0]["name"] == "访客"
    assert "gone" not in str(page["online"])
    assert page["questions"][1]["name"] == "已删除的账号"
    assert page["questions"][1]["cut"] is True


def test_actions_use_chinese_and_hide_secrets() -> None:
    page = board.build(
        [user()],
        [],
        [
            {
                "ts": NOW - 4,
                "user": "u1",
                "tool": "web_search",
                "args": {"query": "助学金", "password": "secret", "api_key": "sk-should-not-show"},
                "ok": False,
                "error": "超时",
            },
            {
                "ts": NOW - 8,
                "user": "u1",
                "tool": "walking_route",
                "args": {"origin": "图书馆", "destination": "食堂"},
                "ok": True,
            },
            {"ts": NOW - 9, "user": "u1", "tool": "jw_secret_thing", "args": {"raw": "内部名"}, "ok": True},
        ],
        NOW,
    )
    texts = [item["text"] for item in page["actions"]]
    assert texts[0] == "用了联网搜索：助学金"
    assert page["actions"][0]["ok"] is False and page["actions"][0]["error"] == "超时"
    assert texts[1] == "查了步行路线：图书馆 → 食堂"
    assert texts[2] == "做了一次操作"
    blob = " ".join(texts)
    assert "jw_secret" not in blob and "secret" not in blob and "sk-" not in blob


def test_only_eighty_rows_are_shown_and_bad_lines_are_skipped() -> None:
    questions = [{"ts": NOW - i, "user": "u1", "question": f"第{i}问", "cut": False} for i in range(100)]
    page = board.build([user(lastLoginAt=None)], questions, [], NOW)
    assert len(page["questions"]) == 80
    assert page["questions"][0]["text"] == "第0问"
    assert board.loads(["{", "not-json", '{"ts": 1, "question": "好"}']) == [{"ts": 1, "question": "好"}]
