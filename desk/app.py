"""平台后台。只监听本机 8300，读学校的用户表和 Redis，不进学生用的页面。

启动（在 smart_campus 目录）:
    python desk/app.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg
import redis
from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

HERE = Path(__file__).resolve().parent
# 后台目录放前面，避免和问答服务里同名的 auth 撞上。
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE))

import board  # noqa: E402
import desk_log  # noqa: E402
import session  # noqa: E402

SCAN = 500
LOGIN_TRIES = 8
LOGIN_WINDOW = 600
COOKIE = "desk_session"
NOTE = "desk_note"
NOTES = {
    "no": "进不了这个后台，请用学校管理员账号。",
    "limit": "尝试次数太多，请 10 分钟后再试",
    "down": "后台暂时连不上",
}
SESSION_SECONDS = session.SESSION_HOURS * 3600
def _font() -> Path:
    for path in (
        HERE / "static" / "marker.woff2",
        HERE.parent / "frontend" / "app" / "fonts" / "LXGWMarkerGothic-subset.woff2",
    ):
        if path.is_file():
            return path
    return HERE / "static" / "marker.woff2"


def _prefix(request: Request) -> str:
    path = request.url.path
    if path == "/desk" or path.startswith("/desk/"):
        return "/desk"
    return ""


def _url(request: Request, path: str) -> str:
    if path == "/":
        return _prefix(request) or "/"
    return f"{_prefix(request)}{path}"


def _cookie_path(request: Request) -> str:
    return _prefix(request) or "/"


app = FastAPI(title="平台小本子", docs_url=None, redoc_url=None, openapi_url=None)
pages = APIRouter()


def _secret() -> str:
    secret = os.getenv("AUTH_JWT_SECRET", "").strip()
    if len(secret) < 16:
        raise RuntimeError("缺少 AUTH_JWT_SECRET")
    return secret


def _redis() -> redis.Redis:
    return redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379"),
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=2,
    )


def _users(tenant_id: str) -> list[dict]:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("缺少 DATABASE_URL")
    with psycopg.connect(url, connect_timeout=3) as conn:
        rows = conn.execute(
            'SELECT id, "studentNo", name, role, disabled, "lastLoginAt" FROM users WHERE "tenantId" = %s',
            (tenant_id,),
        ).fetchall()
    return [
        {"id": row[0], "studentNo": row[1], "name": row[2], "role": row[3], "disabled": row[4], "lastLoginAt": row[5]}
        for row in rows
    ]


def _find_login(slug: str, student_no: str) -> dict | None:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("缺少 DATABASE_URL")
    with psycopg.connect(url, connect_timeout=3) as conn:
        row = conn.execute(
            """
            SELECT u.id, u."studentNo", u.name, u.role, u.disabled, u."passwordHash",
                   t.id, t.name, t.disabled
            FROM users u JOIN tenants t ON t.id = u."tenantId"
            WHERE t.slug = %s AND u."studentNo" = %s
            """,
            (slug.strip(), student_no.strip()),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "studentNo": row[1], "name": row[2], "role": row[3], "disabled": row[4], "passwordHash": row[5],
        "tenantId": row[6], "school": row[7], "tenantDisabled": row[8],
    }


def _rows(key: str) -> list[str]:
    try:
        return list(_redis().lrange(key, 0, SCAN - 1))
    except redis.RedisError:
        return []


def _session(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    try:
        return session.unsign(token, _secret())
    except RuntimeError:
        return None


def _page(name: str, **repl: str) -> HTMLResponse:
    text = (HERE / "static" / name).read_text(encoding="utf-8")
    for key, value in repl.items():
        text = text.replace(f"__{key}__", value)
    return HTMLResponse(text)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _gate(ip: str, slug: str, student_no: str) -> str | None:
    """None 表示可以继续核对密码。按进来的地址和学号分别计数，不看转发头。"""
    try:
        client = _redis()
        who = f"{slug.strip()[:40]}:{student_no.strip()[:32]}"
        key = f"desk:tries:{ip}:{who}"
        count = client.incr(key)
        if count == 1 or client.ttl(key) < 0:
            client.expire(key, LOGIN_WINDOW)
        if int(count) > LOGIN_TRIES:
            return "尝试次数太多，请 10 分钟后再试"
        return None
    except redis.RedisError:
        return "后台暂时连不上"


@app.get("/health")
def health() -> dict[str, str]:
    return {"ok": "true"}


@pages.get("/assets/desk.css")
def desk_css() -> FileResponse:
    return FileResponse(HERE / "static" / "desk.css", media_type="text/css")


@pages.get("/assets/marker.woff2")
def marker_font() -> Response:
    font = _font()
    if not font.is_file():
        return Response(status_code=404)
    return FileResponse(font, media_type="font/woff2")


def _fail(request: Request, code: str) -> RedirectResponse:
    response = RedirectResponse(_url(request, "/login"), status_code=303)
    response.set_cookie(NOTE, code, max_age=60, httponly=True, samesite="lax", path=_cookie_path(request))
    return response


@pages.get("/")
def home(request: Request) -> Response:
    opened = _session(request)
    if opened is None:
        return RedirectResponse(_url(request, "/login"), status_code=303)
    return _page(
        "board.html",
        SCHOOL=_esc(str(opened.get("school") or "")),
        NAME=_esc(str(opened.get("name") or "")),
        ROOT=_prefix(request),
    )


@pages.get("/login")
def login_page(request: Request) -> Response:
    if _session(request) is not None:
        return RedirectResponse(_url(request, "/"), status_code=303)
    message = NOTES.get(request.cookies.get(NOTE, ""), "")
    response = _page("login.html", ERROR=_esc(message), ROOT=_prefix(request))
    response.delete_cookie(NOTE, path=_cookie_path(request))
    return response


@pages.post("/login")
def login(
    request: Request,
    tenant: str = Form(""),
    studentNo: str = Form(""),
    password: str = Form(""),
) -> Response:
    ip = request.client.host if request.client else ""
    blocked = _gate(ip, tenant, studentNo)
    if blocked == "后台暂时连不上":
        return _fail(request, "down")
    if blocked:
        return _fail(request, "limit")
    try:
        found = _find_login(tenant, studentNo)
    except Exception:
        return _fail(request, "down")
    if found is None:
        session.password_ok(password, session.DUMMY_HASH)
        return _fail(request, "no")
    if not session.password_ok(password, found["passwordHash"]):
        return _fail(request, "no")
    if found["disabled"] or found["tenantDisabled"] or found["role"] != "ADMIN":
        return _fail(request, "no")
    token = session.sign(
        {
            "uid": found["id"],
            "tid": found["tenantId"],
            "name": found["name"],
            "school": found["school"],
            "exp": time.time() + SESSION_SECONDS,
        },
        _secret(),
    )
    response = RedirectResponse(_url(request, "/"), status_code=303)
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax", path=_cookie_path(request))
    return response


@pages.post("/logout")
def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse(_url(request, "/login"), status_code=303)
    response.delete_cookie(COOKIE, path=_cookie_path(request))
    return response


@pages.get("/api/feed")
def feed(request: Request) -> JSONResponse:
    opened = _session(request)
    if opened is None:
        return JSONResponse({"detail": "请先登录"}, status_code=401)
    tenant_id = str(opened.get("tid") or "")
    try:
        users = _users(tenant_id)
    except Exception:
        return JSONResponse({"detail": "读不到账号名单"}, status_code=502)
    questions = board.loads(_rows(desk_log.question_key(tenant_id)))
    audits = board.loads(_rows(f"tool_audit:{tenant_id}"))
    body = board.build(users, questions, audits, time.time())
    body["school"] = opened.get("school") or ""
    return JSONResponse(body)


app.include_router(pages)
app.include_router(pages, prefix="/desk")


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv(HERE.parent / "backend" / ".env")
    uvicorn.run(app, host="127.0.0.1", port=8300)
