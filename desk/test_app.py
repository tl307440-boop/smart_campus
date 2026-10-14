"""后台入口：只有管理员能进来，失败原因不区分，提问和操作从 Redis 读出来。"""
from __future__ import annotations

import json

import redis
from fastapi.testclient import TestClient

import app as desk

SECRET = "smoke-secret-0123456789abcdef"
ADMIN = {
    "id": "admin-1", "studentNo": "admin001", "name": "管理员", "role": "ADMIN",
    "disabled": False, "passwordHash": "hashed", "tenantId": "tenant-1",
    "school": "武汉文理学院", "tenantDisabled": False,
}


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key: str, ttl: int) -> bool:
        return True

    def ttl(self, key: str) -> int:
        return 500


def _open(monkeypatch, found: dict | None = ADMIN) -> TestClient:
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    monkeypatch.setattr(desk, "_redis", lambda: FakeRedis())
    monkeypatch.setattr(desk, "_find_login", lambda slug, student_no: found)
    monkeypatch.setattr(desk.session, "password_ok", lambda plain, hashed: plain == "campus2026")
    return TestClient(desk.app)


def test_health_says_nothing_else() -> None:
    body = TestClient(desk.app).get("/health").json()
    assert body == {"ok": "true"}


def test_school_site_path_uses_its_own_cookie(monkeypatch) -> None:
    response = _open(monkeypatch).post(
        "/desk/login",
        data={"tenant": "wtli", "studentNo": "admin001", "password": "campus2026"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/desk")
    issued = response.headers["set-cookie"].lower()
    assert "path=/desk" in issued
    page = TestClient(desk.app).get("/desk/login")
    assert page.status_code == 200
    assert 'action="/desk/login"' in page.text
    assert 'href="/desk/assets/desk.css"' in page.text


def test_feed_needs_a_session() -> None:
    assert TestClient(desk.app).get("/api/feed").status_code == 401


def test_student_and_wrong_password_share_one_sentence(monkeypatch) -> None:
    student = {**ADMIN, "role": "STUDENT"}
    client = _open(monkeypatch, student)
    denied = client.post("/login", data={"tenant": "wtli", "studentNo": "u1", "password": "campus2026"})
    missing = _open(monkeypatch, None).post(
        "/login", data={"tenant": "wtli", "studentNo": "nope", "password": "campus2026"},
    )
    wrong = _open(monkeypatch, ADMIN).post(
        "/login", data={"tenant": "wtli", "studentNo": "admin001", "password": "nope"},
    )
    assert denied.status_code == missing.status_code == wrong.status_code == 200
    assert denied.text == missing.text == wrong.text
    assert "进不了这个后台，请用学校管理员账号。" in denied.text
    assert "desk_session" not in denied.cookies


def test_admin_cookie_is_http_only_and_not_secure(monkeypatch) -> None:
    response = _open(monkeypatch).post(
        "/login",
        data={"tenant": "wtli", "studentNo": "admin001", "password": "campus2026"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    issued = response.headers["set-cookie"].lower()
    assert "desk_session=" in issued
    assert "httponly" in issued
    assert "; secure" not in issued


def test_forwarded_for_does_not_pick_the_rate_key(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    monkeypatch.setattr(desk, "_redis", lambda: fake)
    monkeypatch.setattr(desk, "_find_login", lambda slug, student_no: None)
    monkeypatch.setattr(desk.session, "password_ok", lambda plain, hashed: False)
    TestClient(desk.app).post(
        "/login",
        data={"tenant": "wtli", "studentNo": "a", "password": "b"},
        headers={"x-forwarded-for": "1.2.3.4"},
    )
    assert list(fake.counts) == ["desk:tries:testclient:wtli:a"]


def test_ninth_try_and_redis_down_are_refused(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    monkeypatch.setattr(desk, "_redis", lambda: fake)
    monkeypatch.setattr(desk, "_find_login", lambda slug, student_no: None)
    monkeypatch.setattr(desk.session, "password_ok", lambda plain, hashed: False)
    client = TestClient(desk.app)
    form = {"tenant": "wtli", "studentNo": "a", "password": "b"}
    for _ in range(8):
        assert "10 分钟" not in client.post("/login", data=form).text
    assert "尝试次数太多，请 10 分钟后再试" in client.post("/login", data=form).text

    def down():
        raise redis.ConnectionError("down")

    monkeypatch.setattr(desk, "_redis", down)
    assert "后台暂时连不上" in client.post("/login", data=form).text


def test_feed_reads_questions_and_actions(monkeypatch) -> None:
    client = _open(monkeypatch)
    client.post(
        "/login",
        data={"tenant": "wtli", "studentNo": "admin001", "password": "campus2026"},
    )

    def rows(key: str) -> list[str]:
        if key == "desk:q:tenant-1":
            return [json.dumps({
                "ts": 1_800_000_000, "user": "guest", "question": "<img src=x onerror=alert(1)>", "cut": False,
            })]
        if key == "tool_audit:tenant-1":
            return [json.dumps({
                "ts": 1_800_000_000, "user": "admin-1", "tool": "jw_grades",
                "args": {"password": "nope"}, "ok": True,
            })]
        return []

    monkeypatch.setattr(desk, "_rows", rows)
    monkeypatch.setattr(desk, "_users", lambda tenant_id: [{
        "id": "admin-1", "studentNo": "admin001", "name": "管理员", "role": "ADMIN",
        "disabled": False, "lastLoginAt": None,
    }])
    body = client.get("/api/feed").json()
    assert body["questions"][0]["name"] == "访客"
    assert body["questions"][0]["text"] == "<img src=x onerror=alert(1)>"
    assert body["actions"][0]["text"] == "查了成绩"
    assert "jw_grades" not in body["actions"][0]["text"]
    assert "nope" not in body["actions"][0]["text"]
