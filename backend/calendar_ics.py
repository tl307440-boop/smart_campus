"""课表导出成日历（.ics），以及可订阅的日历链接。

导出是一次性的文件：学生点一下下载，导入手机或电脑的日历。
订阅是一条带令牌的网址：手机日历会定时来取，调课后自己更新，不用再导一次。

安全上的取舍：
- 订阅链接本身就是凭证，谁拿到谁能看这个人的课表，所以令牌只存哈希、只在生成时显示一次，
  学生随时可以重新生成（旧的立刻作废）或关闭；
- 日历软件会反复来取，教务查询又慢，整份日历按令牌缓存半小时，不把学校的教务系统当成手机的轮询对象。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import agent
import jw_account
import redis
from agent import first_monday
from today_card import parse_exam_time
from jw_zhengfang import current_term
from logging_config import get_logger

logger = get_logger(__name__)

TZID = "Asia/Shanghai"
PRODID = "-//smart-campus//timetable//ZH"
ALARM_CHOICES = (0, 5, 10, 15, 30, 60)
DEFAULT_ALARM = 15
TOKEN_TTL = 400 * 86400
FEED_CACHE_SECONDS = 1800
_LINE_OCTETS = 75

_VTIMEZONE = (
    "BEGIN:VTIMEZONE\r\nTZID:Asia/Shanghai\r\nBEGIN:STANDARD\r\nDTSTART:19700101T000000\r\n"
    "TZOFFSETFROM:+0800\r\nTZOFFSETTO:+0800\r\nTZNAME:CST\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
)


class CalendarError(Exception):
    """导不出来，message 可直接给学生看。"""


# ---------------- 生成 .ics ----------------


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545：一行不超过 75 个字节，续行以空格开头；不能把一个汉字切成两半。"""
    if len(line.encode("utf-8")) <= _LINE_OCTETS:
        return line
    parts: list[str] = []
    current = ""
    limit = _LINE_OCTETS
    for char in line:
        if len(current.encode("utf-8")) + len(char.encode("utf-8")) > limit:
            parts.append(current)
            current = char
            limit = _LINE_OCTETS - 1  # 续行开头那个空格占一个字节
        else:
            current += char
    parts.append(current)
    return "\r\n ".join(parts)


def _stamp(day: date, clock: str) -> str:
    hour, minute = clock.split(":")[:2]
    return f"{day.strftime('%Y%m%d')}T{int(hour):02d}{int(minute):02d}00"


def _uid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24] + "@smart-campus"


def _event(lines: list[str], alarm_trigger: str | None, alarm_text: str) -> list[str]:
    out = ["BEGIN:VEVENT", *lines]
    if alarm_trigger:
        out += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(alarm_text)}", f"TRIGGER:{alarm_trigger}", "END:VALARM"]
    out.append("END:VEVENT")
    return out


def _alarm(minutes: int) -> str | None:
    return f"-PT{minutes}M" if minutes else None


def build_calendar(
    data: dict[str, Any], exams: list[dict[str, str]], week1_monday: date, alarm_minutes: int = DEFAULT_ALARM,
    name: str = "我的课表", now: datetime | None = None,
) -> tuple[str, dict[str, int]]:
    """返回 (.ics 文本, 统计)。每节课、每一周各生成一个事件：单双周、1-8 周这类写法就不用翻译成重复规则，
    各家日历软件的解析也都不会出错。UID 由内容生成，重新导入或订阅更新时不会重复。
    """
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    times = {p["period"]: p for p in data.get("periods", [])}
    body: list[str] = []
    lessons = skipped = 0

    for course in data.get("courses", []):
        periods = agent.periods_of(course.get("periods", ""))
        weeks = sorted(agent.weeks_of(course.get("weeks", "")))
        weekday = course.get("weekday", 0)
        start = times.get(str(periods[0]), {}).get("start", "") if periods else ""
        end = times.get(str(periods[-1]), {}).get("end", "") if periods else ""
        if not (periods and weeks and 1 <= weekday <= 7 and start and end):
            skipped += 1  # 节次时间、周次读不出来：不能凭空猜一个时间写进学生的日历
            continue
        place = course.get("room", "")
        teacher = course.get("teacher", "")
        for week in weeks:
            day = week1_monday + timedelta(weeks=week - 1, days=weekday - 1)
            lessons += 1
            body += _event(
                [
                    f"UID:{_uid('lesson', course.get('course', ''), day.isoformat(), start)}",
                    f"DTSTAMP:{stamp}",
                    f"DTSTART;TZID={TZID}:{_stamp(day, start)}",
                    f"DTEND;TZID={TZID}:{_stamp(day, end)}",
                    f"SUMMARY:{_escape(course.get('course', ''))}",
                    *([f"LOCATION:{_escape(place)}"] if place else []),
                    f"DESCRIPTION:{_escape('、'.join(x for x in (f'第{week}周', course.get('periods', ''), teacher) if x))}",
                ],
                _alarm(alarm_minutes), f"{course.get('course', '')} {start} {place}".strip(),
            )

    exam_count = 0
    for exam in exams:
        parsed = parse_exam_time(exam.get("time", ""))
        if not parsed:
            continue
        day, start, end = parsed
        title = f"{exam.get('course', '')} {exam.get('name', '') or '考试'}".strip()
        where = " ".join(x for x in (exam.get("room", ""), f"座位 {exam['seat']}" if exam.get("seat") else "") if x)
        if start and end:
            when = [f"DTSTART;TZID={TZID}:{_stamp(day, start)}", f"DTEND;TZID={TZID}:{_stamp(day, end)}"]
        else:
            when = [f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}"]
        exam_count += 1
        body += _event(
            [
                f"UID:{_uid('exam', exam.get('course', ''), day.isoformat(), start)}",
                f"DTSTAMP:{stamp}",
                *when,
                f"SUMMARY:{_escape(title)}",
                *([f"LOCATION:{_escape(where)}"] if where else []),
            ],
            "-P1D" if alarm_minutes else None, f"明天考试：{title}",
        )

    head = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}", f"X-WR-TIMEZONE:{TZID}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H",
    ]
    text = "".join(_fold(line) + "\r\n" for line in head) + _VTIMEZONE + "".join(_fold(line) + "\r\n" for line in body) + "END:VCALENDAR\r\n"
    return text, {"lessons": lessons, "exams": exam_count, "skipped": skipped}


# ---------------- 从教务取数据 ----------------


def render(tenant_id: str, user_id: str, today: date, alarm_minutes: int = DEFAULT_ALARM, week1_monday: date | None = None) -> tuple[str, dict[str, int]]:
    """取课表和考试，生成 .ics。week1_monday 是开学第一周的周一：教务没给出当前周次（假期）时由学生手填。"""
    if alarm_minutes not in ALARM_CHOICES:
        alarm_minutes = DEFAULT_ALARM
    xnm, xqm = current_term(today)
    with jw_account.source_for(tenant_id, user_id) as jw:
        week = jw.current_week()
        data = jw.timetable(xnm, xqm)
        exams = jw.exams(xnm, xqm)
    if week1_monday is None:
        if not week:
            raise CalendarError("教务现在没有给出「当前是第几周」（可能在假期，或新学期还没开始）。请填一下开学第一周的周一日期再导出。")
        week1_monday = first_monday(today, week)
    elif week1_monday.weekday() != 0:
        raise CalendarError("开学第一周的周一，请选一个星期一的日期。")
    if not data.get("courses"):
        raise CalendarError("教务里这个学期还没有你的课表，等排课后再导出。")
    return build_calendar(data, exams, week1_monday, alarm_minutes)


# ---------------- 订阅令牌 ----------------

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
    return _client


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_key(digest: str) -> str:
    return f"cal:token:{digest}"


def _feed_key(digest: str) -> str:
    return f"cal:feed:{digest}"


def _user_key(tenant_id: str, user_id: str) -> str:
    return f"cal:user:{tenant_id}:{user_id}"


def _drop(digest: str) -> None:
    _redis().delete(_token_key(digest), _feed_key(digest))


def create_subscription(tenant_id: str, user_id: str, alarm_minutes: int = DEFAULT_ALARM) -> str:
    """生成新令牌，同一个人旧的立刻作废。令牌只在这里返回一次。"""
    if alarm_minutes not in ALARM_CHOICES:
        alarm_minutes = DEFAULT_ALARM
    token = secrets.token_urlsafe(32)
    digest = _hash(token)
    store = _redis()
    if old := store.get(_user_key(tenant_id, user_id)):
        _drop(old)
    record = {"tenant": tenant_id, "user": user_id, "alarm": alarm_minutes, "at": time.time()}
    store.set(_token_key(digest), json.dumps(record), ex=TOKEN_TTL)
    store.set(_user_key(tenant_id, user_id), digest, ex=TOKEN_TTL)
    return token


def revoke_subscription(tenant_id: str, user_id: str) -> None:
    store = _redis()
    if old := store.get(_user_key(tenant_id, user_id)):
        _drop(old)
    store.delete(_user_key(tenant_id, user_id))


def subscription_status(tenant_id: str, user_id: str) -> dict[str, Any]:
    digest = _redis().get(_user_key(tenant_id, user_id))
    raw = _redis().get(_token_key(digest)) if digest else None
    if not raw:
        return {"active": False}
    record = json.loads(raw)
    return {"active": True, "alarm": record.get("alarm", DEFAULT_ALARM), "created_at": record.get("at")}


def feed(token: str, today: date) -> str | None:
    """订阅链接来取：令牌无效返回 None；有效就给缓存里的或重新生成的日历。"""
    digest = _hash(token)
    raw = _redis().get(_token_key(digest))
    if not raw:
        return None
    if cached := _redis().get(_feed_key(digest)):
        return cached
    record = json.loads(raw)
    text, _ = render(record["tenant"], record["user"], today, int(record.get("alarm", DEFAULT_ALARM)))
    _redis().set(_feed_key(digest), text, ex=FEED_CACHE_SECONDS)
    return text


__all__ = [
    "ALARM_CHOICES", "CalendarError", "build_calendar", "create_subscription", "feed",
    "render", "revoke_subscription", "subscription_status",
]
