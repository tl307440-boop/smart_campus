"""学生绑定自己的教务账号。

绑定时用学生输入的账号密码登录一次，只把登录后的 Cookie 加密存进 Redis，密码用完即丢。
Cookie 过期（CAS 的「记住我」到期，或学生改了密码）后查询会报 JwAuthError，提示学生重新绑定。
需要验证码时，把 CAS 会话 Cookie 暂存 5 分钟，学生填好验证码后连同账号密码再提交一次。
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import redis
import secret_box
from jw_zhengfang import JwAuthError, JwCaptcha, JwError, ZhengfangClient
from logging_config import get_logger
from workspace import SchoolAi, school_ai

logger = get_logger(__name__)

CRED_TTL = 30 * 86400
PENDING_TTL = 300
BIND_TRIES = 6
BIND_WINDOW = 600
_INFO = b"smart-campus/jw-session"

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
    return _client


def _cred_key(tenant_id: str, user_id: str) -> str:
    return f"jw:cred:{tenant_id}:{user_id}"


def _pending_key(tenant_id: str, user_id: str) -> str:
    return f"jw:pending:{tenant_id}:{user_id}"


def _mask(username: str) -> str:
    return username if len(username) <= 4 else f"{username[:2]}{'*' * (len(username) - 4)}{username[-2:]}"


class JwSource(Protocol):
    def timetable(self, xnm: str, xqm: str) -> dict[str, Any]: ...
    def current_week(self) -> int | None: ...
    def grades(self, xnm: str = "", xqm: str = "") -> list[dict[str, str]]: ...
    def exams(self, xnm: str, xqm: str) -> list[dict[str, str]]: ...
    def free_rooms(self, xnm: str, xqm: str, campus_id: str, week: int, weekday: int, periods: list[int]) -> list[dict[str, str]]: ...


def _new_client(school: SchoolAi) -> ZhengfangClient:
    if school.jw_mode != "zhengfang" or not school.jw_cas_url or not school.jw_base_url:
        raise JwError("学校还没有配置教务系统地址")
    return ZhengfangClient(school.jw_cas_url, school.jw_base_url)


def status(tenant_id: str, user_id: str) -> dict[str, Any]:
    mode = school_ai(tenant_id).jw_mode
    if mode == "demo":
        return {"mode": mode, "bound": True, "username": "演示账号", "boundAt": None}
    raw = _redis().get(_cred_key(tenant_id, user_id)) if mode == "zhengfang" else None
    if not raw:
        return {"mode": mode, "bound": False, "username": "", "boundAt": None}
    meta = json.loads(raw)
    return {"mode": mode, "bound": True, "username": meta.get("username", ""), "boundAt": meta.get("boundAt")}


def probe(tenant_id: str) -> None:
    client = _new_client(school_ai(tenant_id))
    try:
        client.probe()
    finally:
        client.close()


def bind(tenant_id: str, user_id: str, username: str, password: str, captcha: str = "") -> dict[str, Any]:
    """登录并保存 Cookie。需要验证码时抛 JwCaptcha（带图），密码错抛 JwAuthError。"""
    # 别让我们的服务器成了替人撞库的跳板：每人 10 分钟内最多试 6 次
    tries_key = f"jw:tries:{tenant_id}:{user_id}"
    tries = _redis().incr(tries_key)
    if tries == 1:
        _redis().expire(tries_key, BIND_WINDOW)
    if tries > BIND_TRIES:
        raise JwError("尝试次数太多，请 10 分钟后再试")
    client = _new_client(school_ai(tenant_id))
    pending = _redis().get(_pending_key(tenant_id, user_id))
    try:
        if captcha and pending:
            client.load_cookies(json.loads(secret_box.decrypt(pending, _INFO)))
        try:
            client.login(username, password, captcha)
        except JwCaptcha:
            _redis().set(
                _pending_key(tenant_id, user_id),
                secret_box.encrypt(json.dumps(client.export_cookies()), _INFO),
                ex=PENDING_TTL,
            )
            raise
        _redis().delete(_pending_key(tenant_id, user_id), tries_key)
        _save(tenant_id, user_id, client, _mask(username))
    finally:
        client.close()
    logger.info("jw bound", extra={"tenant_id": tenant_id, "user_id": user_id})
    return status(tenant_id, user_id)


def unbind(tenant_id: str, user_id: str) -> None:
    _redis().delete(_cred_key(tenant_id, user_id), _pending_key(tenant_id, user_id))


def _save(tenant_id: str, user_id: str, client: ZhengfangClient, username: str, bound_at: float | None = None) -> None:
    meta = {
        "username": username,
        "boundAt": bound_at or time.time(),
        "cookies": secret_box.encrypt(json.dumps(client.export_cookies()), _INFO),
    }
    _redis().set(_cred_key(tenant_id, user_id), json.dumps(meta), ex=CRED_TTL)


@contextmanager
def source_for(tenant_id: str, user_id: str) -> Iterator[JwSource]:
    """给工具用的教务数据源：演示模式给样例数据；正方模式载入学生的 Cookie，用完把续上的会话存回去。"""
    school = school_ai(tenant_id)
    if school.jw_mode == "demo":
        yield DemoJw()
        return
    if school.jw_mode != "zhengfang":
        raise JwError("学校没有开通教务查询")
    raw = _redis().get(_cred_key(tenant_id, user_id))
    if not raw:
        raise JwAuthError("你还没有绑定教务账号")
    meta = json.loads(raw)
    client = _new_client(school)
    try:
        client.load_cookies(json.loads(secret_box.decrypt(meta["cookies"], _INFO)))
        try:
            yield client
        except JwAuthError:
            unbind(tenant_id, user_id)
            raise
        _save(tenant_id, user_id, client, meta.get("username", ""), meta.get("boundAt"))
    finally:
        client.close()


class DemoJw:
    """演示模式：没对接真实教务时，用固定样例走通整条链路。数据都标明是演示。"""

    def current_week(self) -> int | None:
        return 7

    def timetable(self, xnm: str, xqm: str) -> dict[str, Any]:
        courses = [
            ("高等数学（演示）", 1, "1-2节", "1-16周", "1号教学楼 101", "张老师"),
            ("大学英语（演示）", 1, "3-4节", "1-16周", "2号教学楼 203", "李老师"),
            ("程序设计基础（演示）", 2, "5-6节", "1-12周", "实验楼 305 机房", "王老师"),
            ("思想道德与法治（演示）", 3, "1-2节", "1-8周", "1号教学楼 408", "陈老师"),
            ("体育（演示）", 4, "3-4节", "1-16周", "田径场", "刘老师"),
            ("线性代数（演示）", 5, "1-2节", "2-16周(双)", "1号教学楼 201", "赵老师"),
        ]
        periods = [
            ("1", "08:00", "08:45"), ("2", "08:55", "09:40"), ("3", "10:00", "10:45"), ("4", "10:55", "11:40"),
            ("5", "14:00", "14:45"), ("6", "14:55", "15:40"), ("7", "16:00", "16:45"), ("8", "16:55", "17:40"),
        ]
        return {
            "courses": [
                {"course": c, "weekday": d, "periods": p, "weeks": w, "room": r, "teacher": t} for c, d, p, w, r, t in courses
            ],
            "periods": [{"period": n, "start": s, "end": e} for n, s, e in periods],
            "campus_id": "1",
        }

    def grades(self, xnm: str = "", xqm: str = "") -> list[dict[str, str]]:
        return [
            {"term": "2025-2026-2", "course": "高等数学（演示）", "score": "88", "credit": "4.0", "gpa": "3.80", "type": "必修", "exam": "正常考试"},
            {"term": "2025-2026-2", "course": "大学英语（演示）", "score": "79", "credit": "3.0", "gpa": "2.90", "type": "必修", "exam": "正常考试"},
        ]

    def exams(self, xnm: str, xqm: str) -> list[dict[str, str]]:
        return [{"course": "高等数学（演示）", "time": "2027-01-08(09:00-11:00)", "room": "1号教学楼 101", "campus": "", "name": "期末考试", "seat": "12"}]

    def free_rooms(self, xnm: str, xqm: str, campus_id: str, week: int, weekday: int, periods: list[int]) -> list[dict[str, str]]:
        return [{"room": f"1号教学楼 {n}（演示）", "type": "多媒体教室", "seats": "120"} for n in (102, 205, 306)]
