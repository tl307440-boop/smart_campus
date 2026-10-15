"""学生绑定自己的教务账号。

绑定时用学生输入的账号密码登录一次，只把登录后的 Cookie 加密存进 Redis，密码用完即丢。
Cookie 过期（CAS 的「记住我」到期，或学生改了密码）后查询会报 JwAuthError，提示学生重新绑定。
需要验证码时，把 CAS 会话 Cookie 暂存 5 分钟，学生填好验证码后连同账号密码再提交一次。
也可以用企业微信扫码绑定（学校 CAS 开了企业微信登录时）：二维码和 CAS 会话同样暂存 5 分钟，
前端反复来问扫码状态，学生在企业微信里确认后，服务器拿 code 回 CAS 换登录凭证，全程不经手密码。
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import redis
import secret_box
import wecom_qr
from jw_zhengfang import JwAuthError, JwCaptcha, JwError, ZhengfangClient
from logging_config import get_logger
from workspace import SchoolAi, school_ai

logger = get_logger(__name__)

CRED_TTL = 30 * 86400
PENDING_TTL = 300
BIND_TRIES = 6
BIND_WINDOW = 600
QR_TRIES = 10
POLLS_PER_USER = 2
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


def _qr_key(tenant_id: str, user_id: str, qr_id: str) -> str:
    return f"jw:wecom:{tenant_id}:{user_id}:{qr_id}"


def _limit(key: str, tries: int) -> None:
    count = _redis().incr(key)
    if count == 1:
        _redis().expire(key, BIND_WINDOW)
    if count > tries:
        raise JwError("尝试次数太多，请 10 分钟后再试")


def _mask(username: str) -> str:
    return username if len(username) <= 4 else f"{username[:2]}{'*' * (len(username) - 4)}{username[-2:]}"


class JwSource(Protocol):
    def timetable(self, xnm: str, xqm: str) -> dict[str, Any]: ...
    def current_week(self) -> int | None: ...
    def grades(self, xnm: str = "", xqm: str = "") -> list[dict[str, str]]: ...
    def exams(self, xnm: str, xqm: str) -> list[dict[str, str]]: ...
    def free_rooms(self, xnm: str, xqm: str, campus_id: str, week: int, weekday: int, periods: list[int]) -> list[dict[str, str]]: ...
    def academia(self) -> dict[str, Any]: ...


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
    _limit(tries_key, BIND_TRIES)
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


def wecom_start(tenant_id: str, user_id: str) -> dict[str, Any]:
    """开一张企业微信登录二维码，返回 {"id": 这张码的编号, "qr": PNG base64, "expiresIn": 秒}。

    每张码单独暂存、轮询时带上编号：换码、切页签后旧码那一轮长轮询回来，碰不到新码。
    """
    _limit(f"jw:wecom-tries:{tenant_id}:{user_id}", QR_TRIES)
    client = _new_client(school_ai(tenant_id))
    try:
        qr, image = wecom_qr.start(client.wecom_link())
        pending = {"qr": qr.to_dict(), "cookies": client.export_cookies()}
    finally:
        client.close()
    qr_id = secrets.token_urlsafe(12)
    _redis().set(_qr_key(tenant_id, user_id, qr_id), secret_box.encrypt(json.dumps(pending), _INFO), ex=PENDING_TTL)
    return {"id": qr_id, "qr": base64.b64encode(image).decode(), "expiresIn": PENDING_TTL}


def wecom_poll(tenant_id: str, user_id: str, qr_id: str) -> dict[str, Any]:
    """等一轮扫码结果。status：waiting / scanned / cancelled / expired / bound（附 account）。"""
    key = _qr_key(tenant_id, user_id, qr_id)
    raw = _redis().get(key)
    if not raw:
        return {"status": "expired"}
    pending = json.loads(secret_box.decrypt(raw, _INFO))
    qr = wecom_qr.QrLogin(**pending["qr"])
    # 一次长轮询要占一个工作线程半分钟：每人同时最多两轮（多出来的一般是刚切走的旧码），超了直接报上一轮的状态
    busy_key = f"jw:wecom-polls:{tenant_id}:{user_id}"
    if _redis().incr(busy_key) > POLLS_PER_USER:
        _redis().decr(busy_key)
        return {"status": qr.status or "waiting"}
    _redis().expire(busy_key, 60)
    try:
        state, code = wecom_qr.poll(qr)
    finally:
        _redis().decr(busy_key)
    if state == "expired":
        _redis().delete(key)
        return {"status": state}
    if state != "confirmed" or not code:
        state = "scanned" if state == "confirmed" else state
        if state != qr.status:
            pending["qr"]["status"] = state
            # 长轮询期间这张码可能已到期被删，xx 不让它被这一轮写回来
            _redis().set(key, secret_box.encrypt(json.dumps(pending), _INFO), xx=True, keepttl=True)
        return {"status": state}
    # code 只能用一次：删得掉暂存的那一轮才去换登录态（同一张码在两个页面同时轮询时只有一个能删掉）
    if not _redis().delete(key):
        return {"status": "scanned"}
    client = _new_client(school_ai(tenant_id))
    try:
        client.load_cookies(pending["cookies"])
        username = client.wecom_login(qr.redirect_uri, code, qr.state, qr.appid)
        _save(tenant_id, user_id, client, _mask(username) if username else "企业微信账号")
    finally:
        client.close()
    logger.info("jw bound via wecom", extra={"tenant_id": tenant_id, "user_id": user_id})
    return {"status": "bound", "account": status(tenant_id, user_id)}


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
        return [{"room": f"1号教学楼 {n}（演示）", "building": "1号教学楼", "type": "多媒体教室", "seats": "120"} for n in (102, 205, 306)]

    def academia(self) -> dict[str, Any]:
        return {
            "gpa": 3.21,
            "plan": {"total": 48, "passed": 30, "failed": 1, "not_taken": 12, "in_progress": 5, "extra_passed": 2, "extra_failed": 0},
            "categories": [
                {"name": "通识必修课（演示）", "required": 40.0, "earned": 32.0, "missing": 8.0},
                {"name": "专业必修课（演示）", "required": 60.0, "earned": 35.5, "missing": 24.5},
                {"name": "专业选修课（演示）", "required": 20.0, "earned": 8.0, "missing": 12.0},
            ],
        }
