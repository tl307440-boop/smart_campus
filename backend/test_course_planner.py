"""选课冲突检测与毕业学分缺口沙盘单元测试。"""
from __future__ import annotations

from datetime import date

import course_planner
import jw_account
import pytest
from jw_zhengfang import JwAuthError
from workspace import SchoolAi


def test_parse_helpers() -> None:
    assert course_planner.weeks_of("1-4周,6-8周(双)") == {1, 2, 3, 4, 6, 8}
    assert course_planner.weeks_of([1, 3, 99]) == {1, 3}
    assert course_planner.periods_of("第3、4节") == [3, 4]
    assert course_planner.periods_of("5-8节") == [5, 6, 7, 8]
    assert course_planner.parse_weekday("星期三") == 3
    assert course_planner.parse_weekday("周天") == 7
    assert course_planner.parse_weekday(9) is None


def test_check_course_conflicts_respects_weeks_and_candidate_collisions() -> None:
    timetable = jw_account.DemoJw().timetable("2026", "3")
    # DemoJw 中：
    # - 周三 1-2 节有「思想道德与法治（演示）」，仅 1-8 周上课
    # - 周五 1-2 节有「线性代数（演示）」，仅 2-16(双) 周上课
    non_conflict = course_planner.check_course_conflicts(
        timetable,
        [
            {"name": "心理学导论", "weekday": "周三", "periods": "1-2节", "weeks": "9-16周"},
            {"name": "中国近代史拓展", "weekday": "周五", "periods": "1-2节", "weeks": "1-15周(单)"},
        ],
    )
    assert not non_conflict["has_any_conflict"]

    result = course_planner.check_course_conflicts(
        timetable,
        [
            {"name": "线性代数冲突测试", "weekday": 5, "periods": [1, 2], "weeks": "1-16周"},
            {"name": "候选课A", "weekday": "周二", "periods": "1-2节", "weeks": "1-16周"},
            {"name": "候选课B", "weekday": "周二", "periods": "2-3节", "weeks": "1-16周"},
            {"name": "残缺输入", "weekday": "", "periods": ""},
        ],
    )
    items = {item["name"]: item for item in result["results"]}
    assert items["线性代数冲突测试"]["conflict"]
    assert items["线性代数冲突测试"]["conflicts"][0]["overlap_weeks"] == [2, 4, 6, 8, 10, 12, 14, 16]

    assert items["候选课A"]["conflict"]
    assert items["候选课A"]["candidate_conflicts"][0]["course"] == "候选课B"
    assert items["残缺输入"]["valid"] is False and items["残缺输入"]["conflict"] is False
    assert result["free_windows"]


def test_build_academic_sandbox_demo_and_unbound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(course_planner, "school_ai", lambda tid: SchoolAi(jw_mode="demo"))
    monkeypatch.setattr(jw_account, "school_ai", lambda tid: SchoolAi(jw_mode="demo"))
    sandbox = course_planner.build_academic_sandbox("t1", "u1", date(2026, 10, 12))
    assert sandbox["enabled"] and sandbox["bound"]
    assert sandbox["summary"]["total_required"] == 120.0
    assert sandbox["summary"]["total_earned"] == 75.5
    assert sandbox["summary"]["total_missing"] == 44.5
    assert sandbox["summary"]["completion_pct"] == 63
    assert len(sandbox["categories"]) == 3
    assert sandbox["free_windows"]

    # 未绑定状态返回 bound=False
    class UnboundSource:
        def __enter__(self):
            raise JwAuthError("未绑定")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(jw_account, "source_for", lambda tid, uid: UnboundSource())
    unbound = course_planner.build_academic_sandbox("t1", "u1", date(2026, 10, 12))
    assert unbound == {"enabled": True, "bound": False}

    monkeypatch.setattr(course_planner, "school_ai", lambda tid: SchoolAi(jw_mode="off"))
    assert course_planner.build_academic_sandbox("t1", "u1", date(2026, 10, 12)) == {"enabled": False, "bound": False}
