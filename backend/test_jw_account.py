"""教务账号绑定：只加密存 Cookie、不存密码；验证码续接；过期自动解绑；限制尝试次数。"""
from __future__ import annotations

import json

import httpx
import jw_account
import outbound
import pytest
from jw_zhengfang import JwAuthError, JwCaptcha, JwError, ZhengfangClient
from test_jw_zhengfang import CAS, JW, FakeSchool
from workspace import SchoolAi


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None, keepttl: bool = False, xx: bool = False) -> None:
        if xx and key not in self.data:
            return
        self.data[key] = value
        if ex:
            self.ttl[key] = ex
        elif not keepttl:
            self.ttl.pop(key, None)

    def delete(self, *keys: str) -> int:
        return sum(self.data.pop(key, None) is not None for key in keys)

    def incr(self, key: str) -> int:
        self.data[key] = str(int(self.data.get(key, "0")) + 1)
        return int(self.data[key])

    def decr(self, key: str) -> int:
        self.data[key] = str(int(self.data.get(key, "0")) - 1)
        return int(self.data[key])

    def expire(self, key: str, seconds: int) -> None:
        self.ttl[key] = seconds


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeRedis, FakeSchool]:
    fake, school = FakeRedis(), FakeSchool()
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-0123456789abcdef")
    monkeypatch.setattr(outbound, "_host_is_public", lambda host, port: True)
    monkeypatch.setattr(jw_account, "_redis", lambda: fake)
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="zhengfang", jw_cas_url=CAS, jw_base_url=JW))
    monkeypatch.setattr(jw_account, "_new_client", lambda s: ZhengfangClient(CAS, JW, transport=httpx.MockTransport(school)))
    return fake, school


def test_bind_stores_only_encrypted_cookies(env: tuple[FakeRedis, FakeSchool]) -> None:
    fake, _ = env
    result = jw_account.bind("t1", "u1", "2023001", "secret")
    assert result["bound"] is True and result["username"] == "20***01"
    raw = fake.data["jw:cred:t1:u1"]
    assert "secret" not in raw and "terces" not in raw and "TGC" not in raw
    assert json.loads(raw)["cookies"].startswith("v1.")
    assert fake.ttl["jw:cred:t1:u1"] == jw_account.CRED_TTL
    with jw_account.source_for("t1", "u1") as jw:
        assert jw.grades()[0]["course"] == "高等数学"


def test_captcha_round_trip_keeps_cas_session(env: tuple[FakeRedis, FakeSchool]) -> None:
    fake, school = env
    school.captcha = True
    with pytest.raises(JwCaptcha):
        jw_account.bind("t1", "u1", "2023001", "secret")
    assert "jw:pending:t1:u1" in fake.data and "jw:cred:t1:u1" not in fake.data
    assert jw_account.bind("t1", "u1", "2023001", "secret", "ab12")["bound"] is True
    assert "jw:pending:t1:u1" not in fake.data


def test_wrong_password_is_not_stored_and_tries_are_limited(env: tuple[FakeRedis, FakeSchool]) -> None:
    fake, _ = env
    for _ in range(jw_account.BIND_TRIES):
        with pytest.raises(JwAuthError):
            jw_account.bind("t1", "u1", "2023001", "wrong")
    assert "jw:cred:t1:u1" not in fake.data
    with pytest.raises(JwError, match="次数太多"):
        jw_account.bind("t1", "u1", "2023001", "secret")


def test_expired_credentials_unbind_and_ask_to_rebind(env: tuple[FakeRedis, FakeSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = env
    jw_account.bind("t1", "u1", "2023001", "secret")
    # 换了一所「不认这个 Cookie」的学校：CAS 要求重新登录
    monkeypatch.setattr(jw_account, "_new_client", lambda s: ZhengfangClient(CAS, JW, transport=httpx.MockTransport(FakeSchool())))
    meta = json.loads(fake.data["jw:cred:t1:u1"])
    fake.data["jw:cred:t1:u1"] = json.dumps({**meta, "cookies": jw_account.secret_box.encrypt("[]", jw_account._INFO)})
    with pytest.raises(JwAuthError):
        with jw_account.source_for("t1", "u1") as jw:
            jw.grades()
    assert jw_account.status("t1", "u1")["bound"] is False


def test_cas_outage_keeps_binding(env: tuple[FakeRedis, FakeSchool], monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = env
    jw_account.bind("t1", "u1", "2023001", "secret")
    meta = json.loads(fake.data["jw:cred:t1:u1"])
    fake.data["jw:cred:t1:u1"] = json.dumps({**meta, "cookies": jw_account.secret_box.encrypt("[]", jw_account._INFO)})
    down = httpx.MockTransport(lambda request: httpx.Response(502, text="Bad Gateway"))
    monkeypatch.setattr(jw_account, "_new_client", lambda s: ZhengfangClient(CAS, JW, transport=down))
    with pytest.raises(JwError) as caught:
        with jw_account.source_for("t1", "u1") as jw:
            jw.grades()
    assert not isinstance(caught.value, JwAuthError)
    assert jw_account.status("t1", "u1")["bound"] is True


def test_unbound_student_gets_bind_hint(env: tuple[FakeRedis, FakeSchool]) -> None:
    with pytest.raises(JwAuthError, match="还没有绑定"):
        with jw_account.source_for("t1", "u2"):
            pass


def test_demo_mode_needs_no_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="demo"))
    assert jw_account.status("t1", "u1")["bound"] is True
    with jw_account.source_for("t1", "u1") as jw:
        assert jw.current_week() == 7
        assert all("演示" in c["course"] for c in jw.timetable("2026", "3")["courses"])
