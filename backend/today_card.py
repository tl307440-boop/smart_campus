"""今日卡片：打开聊天页就能看到今天的课和临近的考试，不用先开口问。

数据和工具问答同一来源（教务），只读、只给学生本人。教务查询比较慢，也不该因为学生
反复刷新页面去打扰学校的系统，所以整张卡片按人缓存几分钟；换绑、解绑时由 jw_account 清掉。
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from typing import Any

import agent
import jw_account
import redis
from jw_zhengfang import JwAuthError, current_term
from logging_config import get_logger
from workspace import school_ai

logger = get_logger(__name__)

CACHE_SECONDS = 300
# 只提醒快到的考试：太远的考试放在卡片里只会占地方
EXAM_WINDOW_DAYS = 30
MAX_EXAMS = 3

# 正方的考试时间长这样：2027-01-08(09:00-11:00)，有的学校不带时段
_EXAM_TIME = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:\s*\(?\s*(\d{1,2}:\d{2})\s*[-~—至]\s*(\d{1,2}:\d{2})\s*\)?)?")

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
    return _client


def _cached(key: str, today: date) -> dict[str, Any] | None:
    """每人只留一份：里面写着是哪天的，不是今天（比如跨了零点）就当没有。"""
    try:
        raw = _redis().get(key)
    except redis.RedisError:
        return None
    card = json.loads(raw) if raw else None
    return card if card and card.get("date") == today.isoformat() else None


def _remember(key: str, card: dict[str, Any]) -> None:
    try:
        _redis().set(key, json.dumps(card, ensure_ascii=False), ex=CACHE_SECONDS)
    except redis.RedisError as exc:
        logger.warning(f"[今日卡片] 缓存写入失败: {exc}")


def upcoming_exams(exams: list[dict[str, str]], today: date) -> list[dict[str, Any]]:
    """窗口内、今天及以后的考试，最近的在前。时间读不出来的跳过：宁可少列，不能列错日子。"""
    found = []
    for exam in exams:
        match = _EXAM_TIME.search(exam.get("time", ""))
        if not match:
            continue
        try:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        days_left = (day - today).days
        if not 0 <= days_left <= EXAM_WINDOW_DAYS:
            continue
        found.append({
            "course": exam.get("course", ""), "name": exam.get("name", ""), "date": day.isoformat(),
            "start": match.group(4) or "", "end": match.group(5) or "",
            "room": exam.get("room", ""), "seat": exam.get("seat", ""), "days_left": days_left,
        })
    found.sort(key=lambda e: (e["date"], e["start"]))
    return found[:MAX_EXAMS]


def build_card(tenant_id: str, user_id: str, today: date) -> dict[str, Any]:
    """没开通教务或还没绑定时只返回状态，前端据此决定画什么；教务本身出错则抛出去，由接口翻成提示。"""
    mode = school_ai(tenant_id).jw_mode
    if mode not in ("demo", "zhengfang"):
        return {"enabled": False, "bound": False}

    key = jw_account.today_card_key(tenant_id, user_id)
    if cached := _cached(key, today):
        return cached

    xnm, xqm = current_term(today)
    try:
        with jw_account.source_for(tenant_id, user_id) as jw:
            week = jw.current_week()
            data = jw.timetable(xnm, xqm)
            exams = jw.exams(xnm, xqm)
    except JwAuthError:
        return {"enabled": True, "bound": False}

    lessons = agent.lessons_on(data, today, week)
    card: dict[str, Any] = {
        "enabled": True,
        "bound": True,
        "date": today.isoformat(),
        "weekday": f"星期{agent.WEEKDAYS[today.weekday()]}",
        "week": week,
        "week_unknown": not week,
        "lessons": [
            {k: lesson.get(k, "") for k in ("course", "periods", "start", "end", "room", "teacher")} for lesson in lessons
        ],
        "exams": upcoming_exams(exams, today),
    }
    _remember(key, card)
    return card


def clamp_day(requested: str | None, server_today: date) -> date:
    """前端按学生本机的日期问，免得服务器在别的时区时，凌晨看到的还是昨天的课。

    只信前后一天以内的值，其余一律用服务器的今天。
    """
    if requested:
        try:
            day = date.fromisoformat(requested)
        except ValueError:
            return server_today
        if abs(day - server_today) <= timedelta(days=1):
            return day
    return server_today
