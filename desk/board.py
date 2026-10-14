"""后台页面用的纯数据：在线、提问、操作。不连数据库。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

ONLINE_SECONDS = 300
PAGE_ROWS = 80
_SH = ZoneInfo("Asia/Shanghai")

ROLE_TEXT = {"STUDENT": "学生", "STAFF": "老师", "ADMIN": "管理员"}
TOOL_TEXT = {
    "jw_timetable": "查了课表",
    "jw_exams": "查了考试安排",
    "jw_grades": "查了成绩",
    "jw_free_rooms": "查了空教室",
    "school_news": "查了学校通知",
    "read_notice": "读了通知正文",
    "web_search": "用了联网搜索",
    "campus_place": "找了地点",
    "walking_route": "查了步行路线",
    "search_knowledge": "查了本校资料",
}
_DAY = {"today": "今天", "tomorrow": "明天", "this_week": "本周"}


def loads(rows: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for raw in rows:
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and isinstance(item.get("ts"), (int, float)):
            found.append(item)
    return found


def clock(ts: float) -> str:
    return datetime.fromtimestamp(ts, _SH).strftime("%m/%d %H:%M")


def _ago(ts: float, now: float) -> str:
    delta = max(0, int(now - ts))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{delta // 60} 分钟前"
    return clock(ts)


def _who(user_id: str, users: dict[str, dict[str, Any]]) -> dict[str, str]:
    if user_id in ("", "guest"):
        return {"name": "访客", "studentNo": "", "role": ""}
    user = users.get(user_id)
    if not user:
        return {"name": "已删除的账号", "studentNo": "", "role": ""}
    return {
        "name": str(user.get("name") or ""),
        "studentNo": str(user.get("studentNo") or ""),
        "role": ROLE_TEXT.get(str(user.get("role") or ""), ""),
    }


def _login_ts(user: dict[str, Any]) -> float | None:
    value = user.get("lastLoginAt")
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("UTC"))
        return value.timestamp()
    return float(value)


def _detail(args: object) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("query", "name", "keyword"):
        text = str(args.get(key) or "").strip()
        if text:
            return text[:40]
    origin, destination = str(args.get("origin") or "").strip(), str(args.get("destination") or "").strip()
    if origin or destination:
        return f"{origin} → {destination}"[:40]
    day = _DAY.get(str(args.get("day") or ""))
    return day or ""


def build(users: list[dict[str, Any]], questions: list[dict[str, Any]], audits: list[dict[str, Any]], now: float) -> dict[str, Any]:
    """users 是这所学校的全部账号。提问和操作按新到旧。"""
    by_id = {str(user["id"]): user for user in users if user.get("id")}
    recent = now - ONLINE_SECONDS
    activity: dict[str, tuple[float, str]] = {}

    def touch(user_id: str, ts: float, what: str) -> None:
        if ts < recent:
            return
        user = by_id.get(user_id)
        if user is not None and user.get("disabled"):
            return
        if user is None and user_id not in ("", "guest"):
            return
        key = user_id or "guest"
        previous = activity.get(key)
        if previous is None or ts > previous[0]:
            activity[key] = (ts, what)

    for user in users:
        ts = _login_ts(user)
        if ts is not None:
            touch(str(user["id"]), ts, "登录了")
    for item in questions:
        touch(str(item.get("user") or "guest"), float(item["ts"]), "提问")
    for item in audits:
        verb = TOOL_TEXT.get(str(item.get("tool") or ""), "做了一次操作")
        touch(str(item.get("user") or "guest"), float(item["ts"]), verb)

    online = [
        {**_who(user_id, by_id), "what": what, "ago": _ago(ts, now), "ts": ts}
        for user_id, (ts, what) in activity.items()
    ]
    online.sort(key=lambda row: row["ts"], reverse=True)
    for row in online:
        row.pop("ts")

    shown_questions = []
    for item in questions[:PAGE_ROWS]:
        shown_questions.append({
            **_who(str(item.get("user") or "guest"), by_id),
            "text": str(item.get("question") or ""),
            "cut": bool(item.get("cut")),
            "time": clock(float(item["ts"])),
        })

    shown_actions = []
    for item in audits[:PAGE_ROWS]:
        verb = TOOL_TEXT.get(str(item.get("tool") or ""), "做了一次操作")
        detail = _detail(item.get("args"))
        shown_actions.append({
            **_who(str(item.get("user") or "guest"), by_id),
            "text": f"{verb}：{detail}" if detail else verb,
            "ok": bool(item.get("ok", True)),
            "error": str(item.get("error") or "")[:80],
            "time": clock(float(item["ts"])),
        })

    return {"online": online, "questions": shown_questions, "actions": shown_actions}
