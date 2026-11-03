"""学期回顾：期末/平时生成「本学期学业总结」卡片数据。

涵盖：
1. 本学期修读课程数与课程列表；
2. 累计理论上课节数估算（各课程每周节数 × 截至当前的教学周次）；
3. 最忙碌的一天、最早与最晚排课等学业画像；
4. 绩点与学分统计、学期 GPA 对比趋势；
5. 个性化鼓励语与学业标签。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

import agent
import jw_account
from jw_zhengfang import JwAuthError, current_term
from logging_config import get_logger
from workspace import school_ai

logger = get_logger(__name__)


def _extract_earliest_start(periods_info: list[dict[str, str]], course_periods: list[int]) -> str:
    """根据课程节次找到对应的开始时间"""
    times = {p["period"]: p["start"] for p in periods_info if p.get("period") and p.get("start")}
    for p in course_periods:
        if str(p) in times:
            return times[str(p)]
    return ""


def build_term_review(tenant_id: str, user_id: str, today: date) -> dict[str, Any]:
    """生成本学期回顾数据。未绑定教务或未开通返回 bound=False。"""
    mode = school_ai(tenant_id).jw_mode
    if mode not in ("demo", "zhengfang"):
        return {"enabled": False, "bound": False}

    xnm, xqm = current_term(today)
    try:
        with jw_account.source_for(tenant_id, user_id) as jw:
            week = jw.current_week() or 16  # 若查不到周次，默认按期末 16 周推算
            data = jw.timetable(xnm, xqm)
            grades = jw.grades()
            try:
                academia = jw.academia()
            except Exception:
                academia = {}
    except JwAuthError:
        return {"enabled": True, "bound": False}

    courses = data.get("courses", [])
    periods_def = data.get("periods", [])

    # 1. 课时与周次推算
    # 统计规则：每门课遍历其排课周次，凡周次 <= 当前周，计入课时
    total_lessons_estimated = 0
    weekday_lessons_map = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0}
    earliest_time = "23:59"
    earliest_course = ""
    latest_time = "00:00"
    latest_course = ""

    for c in courses:
        p_list = agent.periods_of(c.get("periods", ""))
        periods_per_session = len(p_list)
        weeks_set = agent.weeks_of(c.get("weeks", ""))
        
        # 统计截至当前教学周已经上过的周数
        past_weeks_count = sum(1 for w in weeks_set if w <= week)
        total_lessons_estimated += past_weeks_count * periods_per_session

        # 统计一周中哪天课最多
        wd = c.get("weekday", 1)
        if wd in weekday_lessons_map:
            weekday_lessons_map[wd] += periods_per_session

        # 计算最早与最晚课
        start_t = _extract_earliest_start(periods_def, p_list)
        if start_t:
            if start_t < earliest_time:
                earliest_time = start_t
                earliest_course = c.get("course", "")
            if start_t > latest_time:
                latest_time = start_t
                latest_course = c.get("course", "")

    # 最忙碌星期几
    busiest_wd = max(weekday_lessons_map, key=lambda k: weekday_lessons_map[k])
    busiest_count = weekday_lessons_map[busiest_wd]
    busiest_day_str = f"星期{agent.WEEKDAYS[busiest_wd - 1]}（每周 {busiest_count} 节）" if busiest_count > 0 else "课程分布均衡"

    # 2. 绩点与学分统计
    terms_gpa = agent.gpa_by_term(grades)
    curr_term_str = f"{xnm}-{xqm}"
    curr_gpa: float | None = None
    prev_gpa: float | None = None
    
    # 找到本学期及上一学期的绩点
    for item in terms_gpa:
        t_name = item.get("term", "")
        if curr_term_str in t_name or (len(terms_gpa) > 0 and item == terms_gpa[-1]):
            curr_gpa = item.get("gpa")
        else:
            prev_gpa = item.get("gpa")

    gpa_change: float | None = None
    if curr_gpa is not None and prev_gpa is not None:
        gpa_change = round(curr_gpa - prev_gpa, 2)

    overall_gpa = academia.get("gpa")

    # 3. 标签与温馨寄语
    tags: list[str] = []
    if total_lessons_estimated >= 120:
        tags.append("课业大户 📚")
    if earliest_time <= "08:15" and earliest_course:
        tags.append("早八战神 ⏰")
    if gpa_change is not None and gpa_change > 0:
        tags.append(f"逆流而上 (+{gpa_change}) 📈")
    if not tags:
        tags.append("厚积薄发 🌱")

    if week >= 14:
        cheer_message = "学期进入冲刺阶段，这一路的积累都将化作考场上的底气，加油！"
    else:
        cheer_message = f"截至第 {week} 周，你已经踏实完成了多门课程的学习，保持节奏，继续前行！"

    term_name = f"{xnm} 学年第 {'一' if xqm == '3' else '二'}学期"

    return {
        "enabled": True,
        "bound": True,
        "term": term_name,
        "current_week": week,
        "total_courses": len(courses),
        "total_lessons_estimated": total_lessons_estimated,
        "busiest_day": busiest_day_str,
        "earliest_class": f"{earliest_time} ({earliest_course})" if earliest_course else "无早课",
        "latest_class": f"{latest_time} ({latest_course})" if latest_course else "无晚课",
        "curr_gpa": curr_gpa,
        "prev_gpa": prev_gpa,
        "gpa_change": gpa_change,
        "overall_gpa": overall_gpa,
        "tags": tags,
        "cheer_message": cheer_message,
        "courses_summary": [
            {
                "course": c.get("course", ""),
                "teacher": c.get("teacher", ""),
                "room": c.get("room", ""),
                "weeks": c.get("weeks", ""),
            }
            for c in courses[:8]
        ],
    }
