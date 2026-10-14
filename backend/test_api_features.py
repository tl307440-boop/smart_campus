"""API 层：长期记忆接口、「记住」指令、网页导入错误映射、访客问答的权限收口。

只打桩，不起 Redis / 向量库 / 大模型（TestClient 不进 with，不触发 lifespan 预载）:
    cd backend && pytest test_api_features.py
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import api
import auth
import jwt
import pytest
from auth import JWT_ALGORITHM, JWT_ISSUER
from fastapi.testclient import TestClient
from knowledge import DocumentInfo
from memory_store import MemoryNote
from web_import import UnsafeUrlError
from workspace import EmbedSettings

SECRET = "test-secret-do-not-use-in-production"
EMBED = EmbedSettings(id="embed-1", tenant_id="tenant-1", daily_limit=50, enabled=True)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    monkeypatch.setattr(auth, "embed_settings", lambda eid: EMBED if eid == EMBED.id else None)
    monkeypatch.setattr(api, "embed_settings", lambda eid: EMBED if eid == EMBED.id else None)
    monkeypatch.setattr(api, "enforce_chat_quota", lambda user_id: None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(api.app)


def headers(role: str = "STUDENT", **overrides: object) -> dict[str, str]:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": "user-1", "tid": "tenant-1", "sno": "20260001", "name": "张三",
        "role": role, "iss": JWT_ISSUER, "iat": now, "exp": now + 3600,
    }
    if role == "GUEST":
        claims.update(sub="guest-embed-1-v1", eid=EMBED.id)
    claims.update(overrides)
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm=JWT_ALGORITHM)}"}


# ---------- 长期记忆 ----------


def test_memories_are_scoped_to_caller(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    lister = MagicMock(return_value=[MemoryNote("m1", "我读大三", 1.0, "auto")])
    monkeypatch.setattr(api.memory_store, "list_memories", lister)

    response = client.get("/api/memories", headers=headers())

    assert response.status_code == 200
    assert response.json() == [{"id": "m1", "content": "我读大三", "created_at": 1.0, "origin": "auto"}]
    lister.assert_called_once_with("tenant-1", "user-1")


def test_add_and_delete_memory(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    adder = MagicMock(return_value=[MemoryNote("m2", "喜欢篮球", 2.0, "manual")])
    monkeypatch.setattr(api.memory_store, "add_memories", adder)
    monkeypatch.setattr(api.memory_store, "delete_memory", lambda t, u, mid: mid == "m2")

    created = client.post("/api/memories", json={"contents": ["喜欢篮球"]}, headers=headers())
    assert created.status_code == 200
    adder.assert_called_once_with("tenant-1", "user-1", ["喜欢篮球"])

    assert client.delete("/api/memories/m2", headers=headers()).status_code == 200
    assert client.delete("/api/memories/nope", headers=headers()).status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/memories"), ("get", "/api/documents"), ("delete", "/api/memories/m1")],
)
def test_guest_is_kept_out_of_member_routes(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path, headers=headers("GUEST")).status_code == 403


def test_workspace_is_readable_by_guest(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from workspace import WorkspaceSettings

    monkeypatch.setattr(api, "workspace_settings", lambda tid: WorkspaceSettings(assistant_name="小文"))
    response = client.get("/api/workspace", headers=headers("GUEST"))

    assert response.status_code == 200
    assert response.json()["assistant_name"] == "小文"


# ---------- 问答 ----------


def test_guest_chat_drops_byok_and_memory(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_events(thread_id, question, tenant_id, llm=None, user_id=""):
        captured.update(thread_id=thread_id, llm=llm, user_id=user_id, tenant_id=tenant_id)
        yield "event: done\ndata: {}\n\n"

    quota = MagicMock()
    monkeypatch.setattr(api, "_chat_events", fake_events)
    monkeypatch.setattr(api, "enforce_embed_quota", quota)

    response = client.post(
        "/api/chat",
        json={"thread_id": "t-1", "question": "门禁几点", "llm": {"api_key": "sk-x"}},
        headers=headers("GUEST"),
    )

    assert response.status_code == 200
    assert captured["llm"] is None
    assert captured["user_id"] == ""
    assert captured["tenant_id"] == "tenant-1"
    quota.assert_called_once_with("embed-1", 50)


def test_member_chat_keeps_user_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_events(thread_id, question, tenant_id, llm=None, user_id=""):
        captured["user_id"] = user_id
        yield "event: done\ndata: {}\n\n"

    quota = MagicMock()
    monkeypatch.setattr(api, "_chat_events", fake_events)
    monkeypatch.setattr(api, "enforce_embed_quota", quota)

    client.post("/api/chat", json={"thread_id": "t-1", "question": "你好"}, headers=headers())

    assert captured["user_id"] == "user-1"
    quota.assert_not_called()


def test_remember_command_is_stored_before_answering(monkeypatch: pytest.MonkeyPatch) -> None:
    adder = MagicMock(return_value=[])
    monkeypatch.setattr(api.memory_store, "add_memories", adder)
    monkeypatch.setattr(api.knowledge_base, "short_corpus", lambda tid: "")

    def no_graph():
        raise RuntimeError("图未就绪")

    monkeypatch.setattr(api, "build_graph", no_graph)

    events = list(api._chat_events("t:u:1", "记住：我读大三", "tenant-1", user_id="user-1"))

    adder.assert_called_once_with("tenant-1", "user-1", ["我读大三"])
    assert "remembered" in events[0]
    assert "error" in events[-1]


def test_remember_command_ignored_for_guest(monkeypatch: pytest.MonkeyPatch) -> None:
    adder = MagicMock()
    monkeypatch.setattr(api.memory_store, "add_memories", adder)
    monkeypatch.setattr(api.knowledge_base, "short_corpus", lambda tid: "")
    monkeypatch.setattr(api, "build_graph", MagicMock(side_effect=RuntimeError("x")))

    list(api._chat_events("t:g:1", "记住：我读大三", "tenant-1", user_id=""))
    adder.assert_not_called()


# ---------- 网页导入 ----------


@pytest.mark.parametrize(
    ("error", "code"),
    [(UnsafeUrlError("内网"), 400), (ValueError("网页返回 404"), 400), (OSError("timeout"), 502)],
)
def test_url_import_error_mapping(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, error: Exception, code: int
) -> None:
    monkeypatch.setattr(api.knowledge_base, "add_url", MagicMock(side_effect=error))
    response = client.post("/api/documents/url", json={"url": "http://x"}, headers=headers("STAFF"))
    assert response.status_code == code


def test_url_import_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    info = DocumentInfo(name="通知.md", chars=10, indexed=True, source="uploaded", tenant_id="tenant-1")
    adder = MagicMock(return_value=info)
    monkeypatch.setattr(api.knowledge_base, "add_url", adder)

    response = client.post(
        "/api/documents/url", json={"url": "https://a.edu.cn/1"}, headers=headers("STAFF")
    )

    assert response.status_code == 200
    assert response.json()["name"] == "通知.md"
    adder.assert_called_once_with("https://a.edu.cn/1", "tenant-1")


@pytest.mark.parametrize(
    ("error", "code"),
    [(api.outbound.UnsafeUrlError("不允许访问内网或保留地址"), 400), (ValueError("Expecting value"), 502)],
)
def test_jw_bind_maps_unsafe_url_and_bad_json(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, error: Exception, code: int
) -> None:
    monkeypatch.setattr(api.jw_account, "bind", MagicMock(side_effect=error))
    response = client.post("/api/jw/account", json={"username": "2023001", "password": "x"}, headers=headers())
    assert response.status_code == code


def test_student_cannot_import_url(client: TestClient) -> None:
    response = client.post("/api/documents/url", json={"url": "https://a.edu.cn"}, headers=headers())
    assert response.status_code == 403
