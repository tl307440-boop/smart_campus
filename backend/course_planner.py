"""选课冲突检测与毕业学分缺口沙盘。

纯粹做课表时段碰撞计算与学业进度聚合：
- 冲突检测按 (教学周, 星期, 节次) 三维精确求交，能识别「单双周错开」「前8周/后8周分段」的不冲突课。
- 学业沙盘直接复用 jw_account.source_for 拉取培养方案学分缺口、成绩与当前课表，不额外存表。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

import jw_account
from jw_zhengfang import JwAuthError, current_term
from workspace import school_ai

WEEKDAYS = "一二三四五六日"
_WEEKDAY_CHARS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7, "七": 7}
_FAIL_WORDS = ("不及格", "不合格", "未通过", "挂科", "缺考", "作弊")
PASS_SCORE = 60
DEFAULT_WEEKS = "1-16周"


def weeks_of(zcd: Any) -> set[int]:
    """「1-8周,10-16周(双)」或整数列表 → 教学周次集合。"""
    if isinstance(zcd, (list, tuple, set)):
        out: set[int] = set()
        for item in zcd:
            try:
                num = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= num <= 30:
                out.add(num)
        return out

    text = str(zcd or "")
    weeks: set[int] = set()
    for part in re.split(r"[,，、;；]", text):
        match = re.search(r"(\d+)(?:\s*[-~～至]\s*(\d+))?\s*周?(?:\s*[\(（]?(单|双)[\)）]?)?", part)
        if not match:
            continue
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        if start > end or start < 1 or end > 30:
            continue
        parity = {"单": 1, "双": 0}.get(match.group(3) or "")
        weeks |= {w for w in range(start, end + 1) if parity is None or w % 2 == parity}
    return weeks


def periods_of(jcs: Any) -> list[int]:
    """「1-2节」「3,4节」或整数列表 → 排序后的节次列表（1..14）。"""
    if isinstance(jcs, (list, tuple, set)):
        out: set[int] = set()
        for item in jcs:
            if isinstance(item, int) and 1 <= item <= 14:
                out.add(item)
            elif isinstance(item, str):
                out.update(periods_of(item))
        return sorted(out)

    text = str(jcs or "")
    periods: set[int] = set()
    for part in re.split(r"[,，、;；]", text):
        match = re.search(r"(\d+)(?:\s*[-~～至]\s*(\d+))?", part)
        if not match:
            continue
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        if 1 <= start <= end <= 14:
            periods.update(range(start, end + 1))
    return sorted(periods)


def parse_weekday(value: Any) -> int | None:
    """将星期一 / 周三 / 3 等输入统一成 1..7。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 7 else None
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        num = int(text)
        return num if 1 <= num <= 7 else None
    for ch, wd in _WEEKDAY_CHARS.items():
        if ch in text:
            return wd
    return None


def is_failed_grade(grade: dict[str, str]) -> bool:
    """成绩单里的一行是不是没通过。"""
    score = str(grade.get("score", "")).strip()
    if any(word in score for word in _FAIL_WORDS):
        return True
    try:
        return float(score) < PASS_SCORE
    except ValueError:
        return False


def _format_spans(nums: list[int]) -> str:
    """[1, 2, 3, 5, 6] → '1-3、5-6'。"""
    if not nums:
        return ""
    sorted_nums = sorted(set(nums))
    spans: list[str] = []
    start = prev = sorted_nums[0]
    for n in sorted_nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            spans.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    spans.append(f"{start}-{prev}" if start != prev else str(start))
    return "、".join(spans)


def _max_period(timetable_data: dict[str, Any]) -> int:
    highest = 8
    for p in timetable_data.get("periods", []):
        try:
            highest = max(highest, int(p.get("period") or 0))
        except (TypeError, ValueError):
            pass
    for c in timetable_data.get("courses", []):
        ps = periods_of(c.get("periods", ""))
        if ps:
            highest = max(highest, max(ps))
    return min(max(highest, 8), 12)


def summarize_timetable(timetable_data: dict[str, Any]) -> dict[str, Any]:
    """统计每周每天的课程占用情况与适合选课的空闲时段。"""
    courses = timetable_data.get("courses", [])
    max_p = _max_period(timetable_data)
    blocks = [("上午", list(range(1, 5))), ("下午", list(range(5, 9)))]
    if max_p >= 9:
        blocks.append(("晚上", list(range(9, max_p + 1))))

    daily_load: list[dict[str, Any]] = []
    free_windows: list[str] = []

    for wd in range(1, 8):
        day_courses = [c for c in courses if c.get("weekday") == wd]
        occupied: set[int] = set()
        items: list[dict[str, Any]] = []
        for c in sorted(day_courses, key=lambda item: periods_of(item.get("periods", ""))[:1] or [0]):
            ps = periods_of(c.get("periods", ""))
            occupied.update(ps)
            items.append({
                "course": c.get("course", ""),
                "periods": c.get("periods", ""),
                "period_nums": ps,
                "weeks": c.get("weeks", ""),
                "week_nums": sorted(weeks_of(c.get("weeks", ""))),
                "room": c.get("room", ""),
                "teacher": c.get("teacher", ""),
            })

        day_label = f"周{WEEKDAYS[wd - 1]}"
        free_blocks: list[str] = []
        if wd <= 5 and not occupied:
            free_blocks.append("全天无课")
            free_windows.append(f"{day_label}全天无课")
        else:
            for block_name, slots in blocks:
                free_slots = [s for s in slots if s not in occupied]
                if len(free_slots) == len(slots):
                    desc = f"{block_name}({slots[0]}-{slots[-1]}节)全空"
                    free_blocks.append(desc)
                    if wd <= 5:
                        free_windows.append(f"{day_label}{desc}")
                elif len(free_slots) >= 2:
                    desc = f"{block_name}({_format_spans(free_slots)}节)空闲"
                    free_blocks.append(desc)
                    if wd <= 5:
                        free_windows.append(f"{day_label}{desc}")

        daily_load.append({
            "weekday": wd,
            "label": day_label,
            "occupied_periods": sorted(occupied),
            "course_count": len(items),
            "courses": items,
            "free_blocks": free_blocks,
        })

    return {"max_period": max_p, "daily_load": daily_load, "free_windows": free_windows}


def _normalize_candidate(raw: dict[str, Any], idx: int) -> dict[str, Any]:
    name = str(raw.get("name") or raw.get("course") or f"候选课程 {idx + 1}").strip()
    wd = parse_weekday(raw.get("weekday"))
    ps = periods_of(raw.get("periods"))
    raw_weeks = raw.get("weeks")
    assumed_weeks = not bool(str(raw_weeks or "").strip())
    ws = weeks_of(raw_weeks if not assumed_weeks else DEFAULT_WEEKS)
    return {
        "name": name,
        "weekday": wd,
        "periods": ps,
        "weeks": sorted(ws),
        "weeks_text": str(raw_weeks or DEFAULT_WEEKS).strip(),
        "assumed_weeks": assumed_weeks,
        "credit": str(raw.get("credit") or "").strip(),
    }


def check_course_conflicts(
    timetable_data: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """检测候选选修/重修课程与当前课表（以及候选课程互相之间）的冲突。"""
    summary = summarize_timetable(timetable_data)
    existing = timetable_data.get("courses", [])
    normalized = [_normalize_candidate(c, i) for i, c in enumerate(candidates)]
    results: list[dict[str, Any]] = []

    for i, cand in enumerate(normalized):
        if cand["weekday"] is None or not cand["periods"] or not cand["weeks"]:
            results.append({
                **cand,
                "valid": False,
                "conflict": False,
                "conflicts": [],
                "candidate_conflicts": [],
                "summary": "时间格式不完整，请提供星期、节次（如 1-2 节）",
            })
            continue

        cand_wd = cand["weekday"]
        cand_ps = set(cand["periods"])
        cand_ws = set(cand["weeks"])

        conflicts: list[dict[str, Any]] = []
        for ex in existing:
            if ex.get("weekday") != cand_wd:
                continue
            ex_ps = set(periods_of(ex.get("periods", "")))
            overlap_ps = sorted(cand_ps & ex_ps)
            if not overlap_ps:
                continue
            ex_ws = weeks_of(ex.get("weeks", ""))
            # 如果教务课表未标周次，保守按全学期重叠提示
            overlap_ws = sorted(cand_ws & ex_ws) if ex_ws else sorted(cand_ws)
            if not overlap_ws:
                continue
            conflicts.append({
                "course": ex.get("course", ""),
                "room": ex.get("room", ""),
                "teacher": ex.get("teacher", ""),
                "weekday": cand_wd,
                "overlap_periods": overlap_ps,
                "overlap_weeks": overlap_ws,
                "existing_weeks": ex.get("weeks", ""),
                "existing_periods": ex.get("periods", ""),
            })

        cand_conflicts: list[dict[str, Any]] = []
        for j, other in enumerate(normalized):
            if i == j or other["weekday"] != cand_wd or not other["periods"] or not other["weeks"]:
                continue
            overlap_ps = sorted(cand_ps & set(other["periods"]))
            overlap_ws = sorted(cand_ws & set(other["weeks"]))
            if overlap_ps and overlap_ws:
                cand_conflicts.append({
                    "course": other["name"],
                    "overlap_periods": overlap_ps,
                    "overlap_weeks": overlap_ws,
                })

        day_label = f"周{WEEKDAYS[cand_wd - 1]}"
        if not conflicts and not cand_conflicts:
            note = "（未指定周次，默认按 1-16 周检测）" if cand["assumed_weeks"] else ""
            text = f"无时间冲突，可在{day_label}第 {_format_spans(cand['periods'])} 节排入课表{note}"
        else:
            parts: list[str] = []
            for item in conflicts:
                parts.append(
                    f"与《{item['course']}》在{day_label}第 {_format_spans(item['overlap_periods'])} 节"
                    f"（第 {_format_spans(item['overlap_weeks'])} 周）冲突"
                )
            for item in cand_conflicts:
                parts.append(
                    f"与候选课《{item['course']}》在{day_label}第 {_format_spans(item['overlap_periods'])} 节"
                    f"（第 {_format_spans(item['overlap_weeks'])} 周）冲突"
                )
            text = "；".join(parts)

        results.append({
            **cand,
            "valid": True,
            "conflict": bool(conflicts or cand_conflicts),
            "conflicts": conflicts,
            "candidate_conflicts": cand_conflicts,
            "summary": text,
        })

    return {
        "results": results,
        "has_any_conflict": any(r["conflict"] for r in results),
        "free_windows": summary["free_windows"],
        "daily_load": summary["daily_load"],
    }


def build_academic_sandbox(tenant_id: str, user_id: str, today: date) -> dict[str, Any]:
    """生成「学业规划与选课沙盘」所需的完整数据。未开通或未绑定时给出明确状态。"""
    mode = school_ai(tenant_id).jw_mode
    if mode not in ("demo", "zhengfang"):
        return {"enabled": False, "bound": False}

    xnm, xqm = current_term(today)
    try:
        with jw_account.source_for(tenant_id, user_id) as jw:
            week = jw.current_week()
            timetable = jw.timetable(xnm, xqm)
            grades = jw.grades()
            try:
                academia = jw.academia()
            except Exception:
                academia = {}
    except JwAuthError:
        return {"enabled": True, "bound": False}

    raw_categories = academia.get("categories") or []
    categories: list[dict[str, Any]] = []
    total_required = 0.0
    total_earned = 0.0
    total_missing = 0.0

    for item in raw_categories:
        req = float(item.get("required") or 0.0)
        earned = float(item.get("earned") or 0.0)
        missing = float(item.get("missing") if item.get("missing") is not None else max(0.0, req - earned))
        total_required += req
        total_earned += earned
        total_missing += max(0.0, missing)
        pct = min(100, round(earned / req * 100)) if req > 0 else 100
        status = "done" if missing <= 0 else "almost" if pct >= 75 else "pending"
        categories.append({
            "name": item.get("name", ""),
            "required": round(req, 1),
            "earned": round(earned, 1),
            "missing": round(max(0.0, missing), 1),
            "pct": pct,
            "status": status,
        })

    completion_pct = min(100, round(total_earned / total_required * 100)) if total_required > 0 else 0
    failed_courses = [
        {"term": g.get("term", ""), "course": g.get("course", ""), "score": g.get("score", ""), "credit": g.get("credit", ""), "type": g.get("type", "")}
        for g in grades
        if is_failed_grade(g)
    ]

    schedule_summary = summarize_timetable(timetable)
    return {
        "enabled": True,
        "bound": True,
        "term": f"{xnm}-{xqm}",
        "current_week": week,
        "gpa": academia.get("gpa"),
        "plan": academia.get("plan"),
        "summary": {
            "total_required": round(total_required, 1),
            "total_earned": round(total_earned, 1),
            "total_missing": round(total_missing, 1),
            "completion_pct": completion_pct,
        },
        "categories": categories,
        "failed_courses": failed_courses,
        "periods": timetable.get("periods", []),
        "max_period": schedule_summary["max_period"],
        "daily_load": schedule_summary["daily_load"],
        "free_windows": schedule_summary["free_windows"],
    }
