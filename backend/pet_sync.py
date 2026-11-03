"""桌宠专属数据同步服务。

仿照日历订阅机制：
1. 学生在网页端生成一个「桌宠专用令牌」（Redis 存储 SHA-256 哈希，支持随手重新生成或注销）；
2. 独立桌宠配置该令牌后，通过只读公开接口 GET /api/pet/feed/{token} 获取今日课表与临近考试提醒；
3. 根据当前时间，提炼出桌宠专属气泡文案（如剩余课数、下节课教室倒计时、明天考试提醒）。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import date, datetime
from typing import Any

import redis
import today_card
from logging_config import get_logger

logger = get_logger(__name__)

TOKEN_TTL = 400 * 86400  # 400 天有效期
_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
    return _client


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_key(digest: str) -> str:
    return f"pet:token:{digest}"


def _user_key(tenant_id: str, user_id: str) -> str:
    return f"pet:user:{tenant_id}:{user_id}"


def create_pet_token(tenant_id: str, user_id: str) -> str:
    """生成桌宠同步令牌，旧令牌自动作废。"""
    token = secrets.token_urlsafe(32)
    digest = _hash(token)
    store = _redis()
    if old := store.get(_user_key(tenant_id, user_id)):
        store.delete(_token_key(old))
    record = {"tenant": tenant_id, "user": user_id, "at": time.time()}
    store.set(_token_key(digest), json.dumps(record), ex=TOKEN_TTL)
    store.set(_user_key(tenant_id, user_id), digest, ex=TOKEN_TTL)
    return token


def revoke_pet_token(tenant_id: str, user_id: str) -> None:
    """撤销桌宠同步令牌。"""
    store = _redis()
    if old := store.get(_user_key(tenant_id, user_id)):
        store.delete(_token_key(old))
    store.delete(_user_key(tenant_id, user_id))


def pet_token_status(tenant_id: str, user_id: str) -> dict[str, Any]:
    """查看当前学生的桌宠令牌状态。"""
    digest = _redis().get(_user_key(tenant_id, user_id))
    raw = _redis().get(_token_key(digest)) if digest else None
    if not raw:
        return {"active": False}
    record = json.loads(raw)
    return {"active": True, "created_at": record.get("at")}


def _time_to_minutes(clock: str) -> int:
    try:
        parts = clock.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return -1


def build_pet_feed(tenant_id: str, user_id: str, today: date, now_clock: str | None = None) -> dict[str, Any]:
    """根据今日课表和考试，为桌宠生成结构化摘要与气泡台词。"""
    card = today_card.build_card(tenant_id, user_id, today)
    if not card.get("enabled") or not card.get("bound"):
        return {
            "bound": False,
            "status_text": "尚未绑定教务系统",
            "bubble_lines": ["去网页端绑定教务，我能帮你记课表哦～ 🐾"],
            "today_lessons": [],
            "upcoming_exams": [],
        }

    lessons = card.get("lessons", [])
    exams = card.get("exams", [])

    if now_clock is None:
        now_dt = datetime.now()
        now_clock = f"{now_dt.hour:02d}:{now_dt.minute:02d}"

    now_min = _time_to_minutes(now_clock)

    # 分析还剩几节课，以及下节课
    lessons_left = 0
    next_lesson: dict[str, Any] | None = None
    for lesson in lessons:
        end_clock = lesson.get("end", "")
        end_min = _time_to_minutes(end_clock) if end_clock else -1
        # 如果还没下课，或者没有精确时间，计为未完成
        if end_min < 0 or end_min >= now_min:
            lessons_left += 1
            if next_lesson is None:
                next_lesson = lesson

    bubble_lines: list[str] = []

    # 1. 考试提醒优先级最高
    tomorrow = today.fromordinal(today.toordinal() + 1)
    for exam in exams:
        exam_date_str = exam.get("date", "")
        if exam_date_str == today.isoformat():
            bubble_lines.append(f"今天有考试：{exam.get('course')} ({exam.get('start')})，冲鸭！")
        elif exam_date_str == tomorrow.isoformat():
            bubble_lines.append(f"明天有考试：{exam.get('course')} ({exam.get('start')})，别忘了复习～")

    # 2. 课程提醒
    if len(lessons) == 0:
        bubble_lines.append("今天完全没有课，尽情摸鱼吧～ 🎉")
    elif lessons_left == 0:
        bubble_lines.append("今天的课全都上完啦，辛苦啦！好好放松一下～ ✨")
    else:
        if next_lesson:
            room = next_lesson.get("room") or "待定教室"
            start = next_lesson.get("start")
            time_str = f" {start}" if start else ""
            bubble_lines.append(f"今天还有 {lessons_left} 节课，下节{time_str}在 {room} 🏃")
        else:
            bubble_lines.append(f"今天还有 {lessons_left} 节课要上哦～")

    # 3. 兜底生活台词
    bubble_lines.append("摸鱼虽好，下节课可别迟到了哦～")

    return {
        "bound": True,
        "date": today.isoformat(),
        "weekday": card.get("weekday", ""),
        "week": card.get("week"),
        "now": now_clock,
        "total_lessons_today": len(lessons),
        "lessons_left": lessons_left,
        "next_lesson": next_lesson,
        "bubble_lines": bubble_lines,
        "today_lessons": lessons,
        "upcoming_exams": exams,
    }


def feed_pet_by_token(token: str, today: date, now_clock: str | None = None) -> dict[str, Any] | None:
    """根据令牌获取桌宠 feed 数据。令牌不存在返回 None。"""
    digest = _hash(token)
    raw = _redis().get(_token_key(digest))
    if not raw:
        return None
    record = json.loads(raw)
    return build_pet_feed(record["tenant"], record["user"], today, now_clock)
