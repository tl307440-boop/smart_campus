"""使用情况统计的回归测试：纯计算，不连库。

    cd backend && pytest test_usage_stats.py
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import usage_stats as stats

NOW = datetime(2026, 10, 27, 12, 0)
REFUSAL = "抱歉，小懒在本校资料里没有找到与这个问题相关的内容。可以换个说法，或者联系学校相关部门确认。"


def row(question: str, outcome: str = "answered", minutes_ago: int = 0, **extra) -> dict:
    return {"question": question, "outcome": outcome, "guest": False, "at": NOW - timedelta(minutes=minutes_ago),
            "rating": None, "reason": "", "answer": "", **extra}


# ---------- 结局判定 ----------


@pytest.mark.parametrize(
    ("intent", "answer", "sources", "tools", "expected"),
    [
        ("COMFORT", "我在听", [], [], "comfort"),
        ("CRISIS", "先别急", [], [], "comfort"),
        ("RAG", REFUSAL, [], [], "refused"),
        ("RAG", "晚上十点关门", [{"source": "手册.md"}], [], "answered"),
        ("RAG", "我猜是十点", [], [], "no_source"),
        ("TOOL", "今天有两节课", [], ["jw_timetable"], "tool"),
        ("DIRECT", "你好呀", [], [], "answered"),
    ],
)
def test_classify(intent: str, answer: str, sources: list, tools: list, expected: str) -> None:
    assert stats.classify(intent, answer, sources, tools, REFUSAL) == expected


def test_refusal_wins_over_tool_outcome() -> None:
    """只按资料回答的学校：工具没查到、模型照说拒答话术，也算没答上来。"""
    assert stats.classify("TOOL", REFUSAL, [], ["search_knowledge"], REFUSAL) == "refused"


def test_chitchat_is_never_a_gap() -> None:
    assert stats.classify("DIRECT", "哈哈", [], [], REFUSAL) == "answered"


# ---------- 归并相似问题 ----------


def test_rephrased_questions_land_in_one_group() -> None:
    groups = stats.cluster([
        row("成绩单怎么补办"),
        row("怎么补办成绩单？"),
        row("请问成绩单怎么补办呢"),
        row("图书馆几点关门"),
    ])
    assert [g.count for g in groups] == [3, 1]
    assert groups[1].text == "图书馆几点关门"


def test_representative_is_the_most_common_wording() -> None:
    groups = stats.cluster([row("补办学生证"), row("学生证怎么补办"), row("学生证怎么补办")])
    assert groups[0].text == "学生证怎么补办"


def test_different_questions_stay_apart() -> None:
    groups = stats.cluster([row("图书馆几点关门"), row("食堂几点开门"), row("宿舍几点熄灯")])
    assert len(groups) == 3


def test_blank_and_punctuation_only_questions_are_ignored() -> None:
    assert stats.cluster([row("？？？"), row("   "), row("")]) == []


def test_groups_sort_by_count_then_recency() -> None:
    groups = stats.cluster([
        row("奖学金怎么评", minutes_ago=50),
        row("请假流程是什么", minutes_ago=5),
        row("校车时刻表在哪看", minutes_ago=1),
        row("校车时刻表在哪看", minutes_ago=2),
    ])
    assert [g.text for g in groups] == ["校车时刻表在哪看", "请假流程是什么", "奖学金怎么评"]


# ---------- 报告 ----------


def audit(tool: str, ok: bool = True, ms: int = 100, ts: float = 1_000_000, error: str = "") -> dict:
    return {"ts": ts, "tool": tool, "ok": ok, "ms": ms, "error": error, "user": "u1", "args": {}}


def report(rows: list[dict], audits: list[dict] | None = None, now: float = 1_000_100) -> dict:
    return stats.build_report(rows, audits or [], {"jw_grades": "查成绩"}, 7, now)


def test_totals_and_answer_rate() -> None:
    result = report([
        row("甲甲甲"), row("乙乙乙", "tool"), row("丙丙丙", "refused"), row("丁丁丁", "no_source"),
        row("", "comfort"),
        row("戊戊戊", rating=1), row("己己己", rating=-1),
    ])
    totals = result["totals"]
    assert totals["questions"] == 7
    assert totals["comfort"] == 1
    assert totals["refused"] == 1 and totals["no_source"] == 1
    assert totals["up"] == 1 and totals["down"] == 1
    # 倾诉不算进答复率：6 条里答上 4 条
    assert totals["answer_rate"] == pytest.approx(4 / 6, abs=0.001)


def test_empty_period_has_no_answer_rate() -> None:
    assert report([])["totals"]["answer_rate"] is None


def test_comfort_questions_never_leave_the_server_side_stats() -> None:
    result = report([row("我最近压力很大睡不着", "comfort"), row("我最近压力很大睡不着", "comfort", rating=-1)])
    assert result["top_questions"] == []
    assert result["unanswered"] == []


def test_unanswered_collects_refusals_gaps_and_downvotes() -> None:
    result = report([
        row("缓考怎么申请", "refused", 3),
        row("怎么申请缓考", "refused", 2),
        row("怎么申请缓考？", "no_source", 1),
        row("图书馆几点关门", rating=-1, reason="outdated", answer="晚上九点"),
        row("食堂在哪", "answered"),
    ])
    gaps = {item["question"]: item for item in result["unanswered"]}
    assert len(gaps) == 2
    deferred = next(item for item in result["unanswered"] if "缓考" in item["question"])
    assert deferred["count"] == 3
    assert deferred["kinds"] == {"refused": 2, "no_source": 1}
    library = gaps["图书馆几点关门"]
    assert library["kinds"] == {"downvoted": 1}
    assert library["reasons"] == {"outdated": 1}
    assert library["answer_hint"] == "晚上九点"
    assert result["unanswered"][0] is deferred


def test_top_questions_are_capped() -> None:
    questions = [
        "图书馆几点关门", "食堂在哪里", "宿舍几点熄灯", "奖学金怎么评", "校车时刻表", "补办学生证",
        "转专业条件", "缓考怎么申请", "四六级报名", "campus wifi 密码", "体测不及格", "毕业论文格式",
    ]
    assert len(report([row(q) for q in questions])["top_questions"]) == stats.TOP_QUESTIONS


def test_tool_stats_count_failures_and_ignore_old_calls() -> None:
    result = report(
        [],
        [
            audit("jw_grades", True, 100),
            audit("jw_grades", False, 300, error="登录过期"),
            audit("jw_grades", False, 200, ts=1_000_050, error="教务系统超时"),
            audit("jw_grades", True, 100, ts=5),  # 超出统计窗口
            audit("web_search", True, 50),
            {"tool": "broken"},  # 脏数据跳过
        ],
        now=1_000_100 + 7 * 86400 - 200_000,
    )
    tools = {tool["name"]: tool for tool in result["tools"]}
    grades = tools["jw_grades"]
    assert grades["label"] == "查成绩"
    assert (grades["calls"], grades["failures"]) == (3, 2)
    assert grades["failure_rate"] == pytest.approx(0.667, abs=0.001)
    assert grades["avg_ms"] == 200
    assert grades["last_error"] == "教务系统超时"
    assert tools["web_search"]["label"] == "web_search"
    assert result["totals"]["tool_calls"] == 4
    assert result["totals"]["tool_failures"] == 2
