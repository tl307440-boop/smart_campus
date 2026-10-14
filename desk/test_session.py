"""登录会话：签名改了就作废，过期就作废，密码只认现有的 Argon2id 散列。"""
from __future__ import annotations

import time

from argon2 import PasswordHasher

import session

HASHER = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1)
SECRET = "smoke-secret-0123456789abcdef"


def test_round_trip_and_rejects_tampering() -> None:
    token = session.sign({"uid": "u1", "exp": time.time() + 60}, SECRET)
    assert session.unsign(token, SECRET)["uid"] == "u1"
    body, sig = token.split(".", 1)
    assert session.unsign(body + "." + ("0" * len(sig)), SECRET) is None
    assert session.unsign(token, "another-secret-0123456789") is None


def test_expired_session_is_rejected() -> None:
    token = session.sign({"uid": "u1", "exp": time.time() - 1}, SECRET)
    assert session.unsign(token, SECRET) is None


def test_password_matches_the_school_hasher() -> None:
    hashed = HASHER.hash("campus2026")
    assert session.password_ok("campus2026", hashed)
    assert session.password_ok("wrong", hashed) is False
    assert session.password_ok("anything", session.DUMMY_HASH) is False
