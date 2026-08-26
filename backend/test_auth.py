"""鉴权层回归测试。

只依赖 fastapi + pyjwt，不碰 Redis / 向量库 / 大模型，可以在 CI 里裸跑:
    cd backend && pytest test_auth.py
"""
from __future__ import annotations

import time

import auth
import jwt
import pytest
from auth import (
    JWT_ALGORITHM,
    JWT_ISSUER,
    CurrentUser,
    CurrentUserDep,
    KnowledgeManagerDep,
    scoped_thread_id,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "test-secret-do-not-use-in-production"


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.get("/any")
    def any_user(user: CurrentUserDep) -> dict[str, str]:
        return {"id": user.id, "role": user.role}

    @app.get("/manage")
    def manager_only(user: KnowledgeManagerDep) -> dict[str, str]:
        return {"id": user.id}

    return TestClient(app)


def make_token(
    *,
    role: str = "STUDENT",
    secret: str = SECRET,
    issuer: str = JWT_ISSUER,
    expires_in: int = 3600,
    **overrides: object,
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": "user-1",
        "tid": "tenant-1",
        "sno": "20260001",
        "name": "张三",
        "role": role,
        "iss": issuer,
        "iat": now,
        "exp": now + expires_in,
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm=JWT_ALGORITHM)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- 缺少 / 无效身份一律 401 ----------


def test_missing_header_is_401(client: TestClient) -> None:
    assert client.get("/any").status_code == 401


def test_wrong_scheme_is_401(client: TestClient) -> None:
    response = client.get("/any", headers={"Authorization": f"Basic {make_token()}"})
    assert response.status_code == 401


def test_wrong_secret_is_401(client: TestClient) -> None:
    response = client.get("/any", headers=bearer(make_token(secret="another-secret")))
    assert response.status_code == 401


def test_expired_token_is_401(client: TestClient) -> None:
    response = client.get("/any", headers=bearer(make_token(expires_in=-10)))
    assert response.status_code == 401


def test_foreign_issuer_is_401(client: TestClient) -> None:
    response = client.get("/any", headers=bearer(make_token(issuer="someone-else")))
    assert response.status_code == 401


def test_missing_claim_is_401(client: TestClient) -> None:
    """签名合法但少了 tid，说明不是本系统签发的令牌。"""
    response = client.get("/any", headers=bearer(make_token(tid="")))
    assert response.status_code == 401


def test_alg_none_is_rejected(client: TestClient) -> None:
    """经典的 alg=none 降级攻击必须挡住。"""
    forged = jwt.encode(
        {"sub": "user-1", "tid": "t", "sno": "1", "name": "x", "role": "ADMIN",
         "iss": JWT_ISSUER, "exp": int(time.time()) + 3600},
        key="",
        algorithm="none",
    )
    assert client.get("/manage", headers=bearer(forged)).status_code == 401


# ---------- 角色边界 ----------


def test_student_passes_any_user_route(client: TestClient) -> None:
    response = client.get("/any", headers=bearer(make_token(role="STUDENT")))
    assert response.status_code == 200
    assert response.json() == {"id": "user-1", "role": "STUDENT"}


def test_student_cannot_manage_knowledge(client: TestClient) -> None:
    response = client.get("/manage", headers=bearer(make_token(role="STUDENT")))
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["STAFF", "ADMIN"])
def test_staff_and_admin_can_manage_knowledge(client: TestClient, role: str) -> None:
    response = client.get("/manage", headers=bearer(make_token(role=role)))
    assert response.status_code == 200


def test_unknown_role_cannot_manage_knowledge(client: TestClient) -> None:
    response = client.get("/manage", headers=bearer(make_token(role="SUPERUSER")))
    assert response.status_code == 403


# ---------- 会话归属 ----------


def test_thread_id_is_namespaced_per_user() -> None:
    """同一个浏览器 thread_id 落到不同用户名下，必须互相看不见。"""
    alice = CurrentUser(id="u1", tenant_id="t1", student_no="1", name="A", role="STUDENT")
    bob = CurrentUser(id="u2", tenant_id="t1", student_no="2", name="B", role="STUDENT")
    shared = "11111111-2222-3333-4444-555555555555"

    assert scoped_thread_id(alice, shared) != scoped_thread_id(bob, shared)
    assert scoped_thread_id(alice, shared).startswith("t1:u1:")


# ---------- 密钥缺失时不得放行 ----------


def test_missing_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="AUTH_JWT_SECRET"):
        auth.jwt_secret()
