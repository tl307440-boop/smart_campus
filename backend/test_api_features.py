"""API 层：长期记忆接口、「记住」指令、网页导入错误映射、访客问答的权限收口。

只打桩，不起 Redis / 向量库 / 大模型（TestClient 不进 with，不触发 lifespan 预载）:
    cd backend && pytest test_api_features.py
"""
from __future__ import annotations

import json
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
    monkeypatch.setattr(api.desk_log, "remember_question", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth, "embed_settings", lambda eid: EMBED if eid == EMBED.id else None)
    monkeypatch.setattr(api, "embed_settings", lambda eid: EMBED if eid == EMBED.id else None)
    monkeypatch.setattr(api, "enforce_chat_quota", lambda user_id: None)
    # 默认不碰真实库；需要库的用例自己打开
    monkeypatch.setattr(api.chat_history, "enabled", lambda: False)


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
    assert response.json() == [{
        "id": "m1",
        "content": "我读大三",
        "created_at": 1.0,
        "origin": "auto",
        "kind": "fact",
        "slot": "",
        "pinned": False,
        "sensitive": False,
    }]
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


def test_update_memory_can_pin_and_edit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    note = MemoryNote("m3", "我读大四", 3.0, "manual", slot="grade", pinned=True)
    updater = MagicMock(return_value=note)
    monkeypatch.setattr(api.memory_store, "update_memory", updater)

    response = client.patch(
        "/api/memories/m3", json={"content": "我读大四", "pinned": True}, headers=headers()
    )

    assert response.status_code == 200
    assert response.json()["pinned"] is True
    updater.assert_called_once_with("tenant-1", "user-1", "m3", content="我读大四", pinned=True)


def test_update_memory_needs_something_to_change(client: TestClient) -> None:
    assert client.patch("/api/memories/m3", json={}, headers=headers()).status_code == 400


def test_clear_memories(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.memory_store, "clear_memories", lambda t, u: 4)

    response = client.delete("/api/memories", headers=headers())

    assert response.status_code == 200
    assert response.json() == {"status": "cleared", "removed": 4}


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

    def fake_events(thread_id, question, tenant_id, llm=None, user_id="", session_id="", replace_question=None, images=None):
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


def test_chat_records_the_question_before_answering(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_events(thread_id, question, tenant_id, llm=None, user_id="", session_id="", replace_question=None, images=None):
        yield "event: done\ndata: {}\n\n"

    def record(tenant_id: str, user_id: str, question: str) -> None:
        seen["tenant_id"] = tenant_id
        seen["user_id"] = user_id
        seen["question"] = question

    monkeypatch.setattr(api, "_chat_events", fake_events)
    monkeypatch.setattr(api.desk_log, "remember_question", record)
    response = client.post("/api/chat", json={"thread_id": "t-1", "question": "今天开门了吗"}, headers=headers())
    assert response.status_code == 200
    assert seen == {"tenant_id": "tenant-1", "user_id": "user-1", "question": "今天开门了吗"}


def test_chat_still_answers_when_the_question_log_breaks(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_events(thread_id, question, tenant_id, llm=None, user_id="", session_id="", replace_question=None, images=None):
        yield "event: done\ndata: {}\n\n"

    def broken(tenant_id: str, user_id: str, question: str) -> None:
        raise RuntimeError("redis gone")

    monkeypatch.setattr(api, "_chat_events", fake_events)
    monkeypatch.setattr(api.desk_log, "remember_question", broken)
    response = client.post("/api/chat", json={"thread_id": "t-1", "question": "还在吗"}, headers=headers())
    assert response.status_code == 200


def test_member_chat_keeps_user_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_events(thread_id, question, tenant_id, llm=None, user_id="", session_id="", replace_question=None, images=None):
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


# ---------- 会话历史 ----------


def _history_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.chat_history, "enabled", lambda: True)
    # 对话测试不连真库：提问记录和取回答编号都换成空操作
    monkeypatch.setattr(api.usage_log, "record_question", lambda *args: None)
    monkeypatch.setattr(api.chat_history, "message_id_at", lambda *args: None)


def _fake_graph(messages: list[dict]) -> MagicMock:
    graph = MagicMock()
    graph.get_state.return_value = MagicMock(values={"messages": messages})
    return graph


QUESTION = {"role": "user", "content": "图书馆几点关门"}
ANSWER = {"role": "assistant", "content": "晚上十点", "sources": [], "tables": []}


def test_session_list_is_scoped_and_empty_for_guest(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    _history_on(monkeypatch)
    lister = MagicMock(return_value=[api.chat_history.SessionInfo("s1", "图书馆几点关门", datetime(2026, 10, 15, tzinfo=timezone.utc))])
    monkeypatch.setattr(api.chat_history, "list_sessions", lister)

    response = client.get("/api/sessions?limit=20", headers=headers())
    assert response.json() == [{"thread_id": "s1", "title": "图书馆几点关门", "updated_at": "2026-10-15T00:00:00+00:00"}]
    lister.assert_called_once_with("tenant-1", "user-1", 20)

    assert client.get("/api/sessions", headers=headers("GUEST")).json() == []
    assert client.get("/api/sessions?limit=500", headers=headers()).status_code == 422


def test_messages_prefer_database(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    monkeypatch.setattr(api.chat_history, "load_messages", lambda t, u, s: [QUESTION, {**ANSWER, "tables": [{"title": "空教室"}]}])
    monkeypatch.setattr(api, "build_graph", MagicMock(side_effect=AssertionError("不该读 Redis")))

    body = client.get("/api/sessions/s1/messages", headers=headers()).json()
    assert [m["content"] for m in body] == ["图书馆几点关门", "晚上十点"]
    assert body[1]["tables"] == [{"title": "空教室"}]


def test_deleted_session_is_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    monkeypatch.setattr(api.chat_history, "load_messages", lambda t, u, s: None)
    monkeypatch.setattr(api.chat_history, "is_blocked", lambda t, u, s: True)
    monkeypatch.setattr(api, "_chat_events", MagicMock(side_effect=AssertionError("不该开始回答")))

    assert client.get("/api/sessions/s1/messages", headers=headers()).status_code == 404
    assert client.post("/api/chat", json={"thread_id": "s1", "question": "在吗"}, headers=headers()).status_code == 404


def test_redis_only_session_is_backfilled_without_failed_turns(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    failed = {"role": "assistant", "content": f"{api.FAILED_PREFIX} 超时"}
    monkeypatch.setattr(api.chat_history, "load_messages", lambda t, u, s: None)
    monkeypatch.setattr(api.chat_history, "is_blocked", lambda t, u, s: False)
    monkeypatch.setattr(api, "build_graph", lambda: _fake_graph([{"role": "user", "content": "在吗"}, failed, QUESTION, ANSWER]))
    backfill = MagicMock(return_value=True)
    monkeypatch.setattr(api.chat_history, "backfill", backfill)

    body = client.get("/api/sessions/s1/messages", headers=headers()).json()

    assert len(body) == 4
    backfill.assert_called_once_with("tenant-1", "user-1", "s1", [QUESTION, ANSWER])


def test_messages_fall_back_to_redis_when_database_breaks(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    monkeypatch.setattr(api.chat_history, "load_messages", MagicMock(side_effect=OSError("db down")))
    monkeypatch.setattr(api, "build_graph", lambda: _fake_graph([QUESTION, ANSWER]))

    assert len(client.get("/api/sessions/s1/messages", headers=headers()).json()) == 2


def test_guest_messages_only_read_redis(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    monkeypatch.setattr(api.chat_history, "load_messages", MagicMock(side_effect=AssertionError("访客不进库")))
    monkeypatch.setattr(api, "build_graph", lambda: _fake_graph([QUESTION, ANSWER]))

    assert len(client.get("/api/sessions/s1/messages", headers=headers("GUEST")).json()) == 2


def test_delete_session_writes_tombstone_and_drops_redis(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    deleter = MagicMock(side_effect=lambda t, u, s: s == "s1")
    monkeypatch.setattr(api.chat_history, "delete_session", deleter)
    graph = _fake_graph([])
    monkeypatch.setattr(api, "build_graph", lambda: graph)

    assert client.delete("/api/sessions/s1", headers=headers()).status_code == 204
    graph.checkpointer.delete_thread.assert_called_once_with("tenant-1:user-1:s1")
    assert client.delete("/api/sessions/other", headers=headers()).status_code == 404
    assert client.delete("/api/sessions/s1", headers=headers("GUEST")).status_code == 403


def test_member_chat_passes_session_id_only_with_database(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_events(thread_id, question, tenant_id, llm=None, user_id="", session_id="", replace_question=None, images=None):
        captured["session_id"] = session_id
        yield "event: done\ndata: {}\n\n"

    monkeypatch.setattr(api, "_chat_events", fake_events)
    client.post("/api/chat", json={"thread_id": "s1", "question": "你好"}, headers=headers())
    assert captured["session_id"] == ""

    _history_on(monkeypatch)
    monkeypatch.setattr(api.chat_history, "is_blocked", lambda t, u, s: False)
    client.post("/api/chat", json={"thread_id": "s1", "question": "你好"}, headers=headers())
    assert captured["session_id"] == "s1"


def _stream_graph(final_messages: list[dict], order: list[str]) -> MagicMock:
    graph = _fake_graph(final_messages)

    def stream(input_state, config, stream_mode):
        order.append("stream")
        yield ("updates", {"finalize": {"final_answer": final_messages[-1]["content"]}})

    graph.stream.side_effect = stream
    return graph


def test_successful_turn_is_saved_before_done(monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    order: list[str] = []
    monkeypatch.setattr(api.knowledge_base, "short_corpus", lambda tid: "")
    monkeypatch.setattr(api.chat_history, "summary", lambda t, u, s: "学生读大三")
    monkeypatch.setattr(api.chat_history, "record_turn", lambda *args: order.append("record") or 2)
    scheduled = MagicMock()
    monkeypatch.setattr(api.session_summary, "schedule", scheduled)
    graph = _stream_graph([QUESTION, ANSWER], order)
    monkeypatch.setattr(api, "build_graph", lambda: graph)

    for frame in api._chat_events("tenant-1:user-1:s1", "图书馆几点关门", "tenant-1", user_id="user-1", session_id="s1"):
        if frame.startswith("event: done"):
            order.append("done")

    assert order == ["stream", "record", "done"]
    assert graph.stream.call_args.args[0]["summary"] == "学生读大三"
    scheduled.assert_called_once()


def test_failed_turn_is_not_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    monkeypatch.setattr(api.knowledge_base, "short_corpus", lambda tid: "")
    monkeypatch.setattr(api.chat_history, "summary", lambda t, u, s: "")
    recorder = MagicMock()
    monkeypatch.setattr(api.chat_history, "record_turn", recorder)
    failed = {"role": "assistant", "content": api.NOT_CONFIGURED}
    monkeypatch.setattr(api, "build_graph", lambda: _stream_graph([QUESTION, failed], []))

    list(api._chat_events("tenant-1:user-1:s1", "图书馆几点关门", "tenant-1", user_id="user-1", session_id="s1"))
    recorder.assert_not_called()


def _rewind_graph(messages: list[dict]) -> MagicMock:
    graph = _stream_graph([*messages], [])
    graph.get_state.return_value = MagicMock(values={"messages": messages})
    return graph


def test_regenerate_drops_last_turn_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    monkeypatch.setattr(api.knowledge_base, "short_corpus", lambda tid: "")
    monkeypatch.setattr(api.chat_history, "summary", lambda t, u, s: "")
    monkeypatch.setattr(api.chat_history, "record_turn", lambda *args: None)
    dropper = MagicMock(return_value=True)
    monkeypatch.setattr(api.chat_history, "drop_last_turn", dropper)
    earlier = [{"role": "user", "content": "在吗"}, {"role": "assistant", "content": "在"}]
    graph = _rewind_graph([*earlier, QUESTION, ANSWER])
    monkeypatch.setattr(api, "build_graph", lambda: graph)

    list(api._chat_events("tenant-1:user-1:s1", "图书馆几点关门", "tenant-1", user_id="user-1",
                          session_id="s1", replace_question="图书馆几点关门"))

    graph.update_state.assert_called_once()
    assert graph.update_state.call_args.args[1] == {"messages": earlier}
    dropper.assert_called_once_with("tenant-1", "user-1", "s1", "图书馆几点关门")


def test_rewind_ignores_a_question_that_is_not_last(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.knowledge_base, "short_corpus", lambda tid: "")
    graph = _rewind_graph([QUESTION, ANSWER])
    monkeypatch.setattr(api, "build_graph", lambda: graph)

    list(api._chat_events("t:g:1", "别的", "tenant-1", replace_question="早就问过的"))
    graph.update_state.assert_not_called()


def test_resending_a_failed_turn_keeps_database_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    monkeypatch.setattr(api.knowledge_base, "short_corpus", lambda tid: "")
    monkeypatch.setattr(api.chat_history, "summary", lambda t, u, s: "")
    monkeypatch.setattr(api.chat_history, "record_turn", lambda *args: None)
    dropper = MagicMock()
    monkeypatch.setattr(api.chat_history, "drop_last_turn", dropper)
    failed = {"role": "assistant", "content": f"{api.FAILED_PREFIX} 超时"}
    graph = _rewind_graph([QUESTION, failed])
    monkeypatch.setattr(api, "build_graph", lambda: graph)

    list(api._chat_events("tenant-1:user-1:s1", "图书馆几点关门", "tenant-1", user_id="user-1",
                          session_id="s1", replace_question="图书馆几点关门"))

    assert graph.update_state.call_args.args[1] == {"messages": []}
    dropper.assert_not_called()


def test_empty_redis_is_restored_from_database(monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    monkeypatch.setattr(api.knowledge_base, "short_corpus", lambda tid: "")
    monkeypatch.setattr(api.chat_history, "summary", lambda t, u, s: "")
    monkeypatch.setattr(api.chat_history, "recent_messages", lambda t, u, s, limit: [QUESTION, ANSWER])
    monkeypatch.setattr(api.chat_history, "record_turn", lambda *args: None)
    graph = _stream_graph([QUESTION, ANSWER], [])
    graph.get_state.side_effect = [MagicMock(values={}), MagicMock(values={"messages": [QUESTION, ANSWER]})]
    monkeypatch.setattr(api, "build_graph", lambda: graph)

    list(api._chat_events("tenant-1:user-1:s1", "那周末呢", "tenant-1", user_id="user-1", session_id="s1"))
    assert graph.stream.call_args.args[0]["messages"] == [QUESTION, ANSWER]


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


def test_jw_bind_hands_captcha_to_student(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.jw_account, "bind", MagicMock(side_effect=api.JwCaptcha("aW1n")))
    response = client.post("/api/jw/account", json={"username": "2023001", "password": "x"}, headers=headers())
    assert response.status_code == 200 and response.json() == {"captcha": "aW1n"}


@pytest.mark.parametrize(
    ("error", "code"),
    [(api.JwAuthError("该企业微信账号未关联学号"), 400), (api.JwError("企业微信扫码页打不开"), 502), (api.outbound.UnsafeUrlError("x"), 400)],
)
def test_jw_wecom_routes_map_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch, error: Exception, code: int) -> None:
    monkeypatch.setattr(api.jw_account, "wecom_start", MagicMock(side_effect=error))
    poll = MagicMock(side_effect=error)
    monkeypatch.setattr(api.jw_account, "wecom_poll", poll)
    assert client.post("/api/jw/account/wecom", headers=headers()).status_code == code
    assert client.get("/api/jw/account/wecom", params={"id": "q1"}, headers=headers()).status_code == code
    poll.assert_called_once_with("tenant-1", "user-1", "q1")
    assert client.get("/api/jw/account/wecom", headers=headers()).status_code == 422
    assert client.post("/api/jw/account/wecom", headers=headers("GUEST")).status_code == 403


def test_student_cannot_import_url(client: TestClient) -> None:
    response = client.post("/api/documents/url", json={"url": "https://a.edu.cn"}, headers=headers())
    assert response.status_code == 403


# ---------- 回答评价与使用情况 ----------


def _done_payload(frames: list[str]) -> dict:
    frame = next(f for f in frames if f.startswith("event: done"))
    return json.loads(frame.split("data: ", 1)[1])


def _answering_graph(messages: list[dict]) -> MagicMock:
    """get_state 给出这几条消息，流里带一个 generate 节点的最终回答（done 里的 answer 取自它）。"""
    graph = _fake_graph(messages)

    def stream(input_state, config, stream_mode):
        yield ("updates", {"generate": {"final_answer": messages[-1]["content"]}})

    graph.stream.side_effect = stream
    return graph


def _logging_turn(monkeypatch: pytest.MonkeyPatch, answer: dict | None = None) -> list[tuple]:
    """会话入库、记录提问都换成可观察的假货，返回被记下的提问。"""
    _history_on(monkeypatch)
    logged: list[tuple] = []
    monkeypatch.setattr(api.knowledge_base, "short_corpus", lambda tid: "")
    monkeypatch.setattr(api.chat_history, "summary", lambda t, u, s: "")
    monkeypatch.setattr(api.chat_history, "record_turn", lambda *args: 2)
    monkeypatch.setattr(api.chat_history, "message_id_at", lambda *args: "m-42")
    monkeypatch.setattr(api.session_summary, "schedule", MagicMock())
    monkeypatch.setattr(api.usage_log, "record_question", lambda *args: logged.append(args))
    monkeypatch.setattr(api, "refusal_text", lambda tenant_id: "抱歉没找到")
    graph = _answering_graph([QUESTION, answer or ANSWER])
    monkeypatch.setattr(api, "build_graph", lambda: graph)
    return logged


def test_done_carries_the_saved_answer_id_and_the_question_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    logged = _logging_turn(monkeypatch)

    frames = list(api._chat_events("tenant-1:user-1:s1", "图书馆几点关门", "tenant-1", user_id="user-1", session_id="s1"))

    assert _done_payload(frames)["message_id"] == "m-42"
    assert logged == [("tenant-1", "图书馆几点关门", "answered", [], False, "m-42")]


def test_refused_answer_is_logged_as_a_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    logged = _logging_turn(monkeypatch, {"role": "assistant", "content": "抱歉没找到"})
    list(api._chat_events("tenant-1:user-1:s1", "缓考怎么申请", "tenant-1", user_id="user-1", session_id="s1"))
    assert logged[0][2] == "refused"


def test_guest_question_is_logged_without_an_answer_id(monkeypatch: pytest.MonkeyPatch) -> None:
    logged = _logging_turn(monkeypatch)
    frames = list(api._chat_events("tenant-1:guest:s1", "新生怎么报到", "tenant-1"))
    assert "message_id" not in _done_payload(frames)
    assert logged == [("tenant-1", "新生怎么报到", "answered", [], True, None)]


def test_regenerating_does_not_count_the_question_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    logged = _logging_turn(monkeypatch)
    graph = _answering_graph([QUESTION, ANSWER])
    monkeypatch.setattr(api, "build_graph", lambda: graph)
    monkeypatch.setattr(api.chat_history, "drop_last_turn", lambda *args: True)

    list(api._chat_events("tenant-1:user-1:s1", "图书馆几点关门", "tenant-1", user_id="user-1",
                          session_id="s1", replace_question="图书馆几点关门"))
    assert logged == []

    list(api._chat_events("tenant-1:user-1:s1", "图书馆周末几点关门", "tenant-1", user_id="user-1",
                          session_id="s1", replace_question="图书馆几点关门"))
    assert len(logged) == 1


def test_failed_answer_is_not_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    logged = _logging_turn(monkeypatch, {"role": "assistant", "content": api.NOT_CONFIGURED})
    list(api._chat_events("tenant-1:user-1:s1", "图书馆几点关门", "tenant-1", user_id="user-1", session_id="s1"))
    assert logged == []


def test_a_broken_usage_log_never_breaks_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _logging_turn(monkeypatch)

    def boom(*args):
        raise OSError("数据库挂了")

    monkeypatch.setattr(api.usage_log, "record_question", boom)
    frames = list(api._chat_events("tenant-1:user-1:s1", "图书馆几点关门", "tenant-1", user_id="user-1", session_id="s1"))
    assert _done_payload(frames)["answer"] == "晚上十点"


def test_feedback_is_for_members_with_a_known_message(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"message_id": "m-1", "rating": -1, "reason": "outdated"}
    assert client.post("/api/feedback", json=body, headers=headers("GUEST")).status_code == 403
    assert client.post("/api/feedback", json=body, headers=headers()).status_code == 503  # 没配数据库

    _history_on(monkeypatch)
    calls: list[tuple] = []
    monkeypatch.setattr(api.usage_log, "set_feedback", lambda *args: calls.append(args) or True)
    assert client.post("/api/feedback", json=body, headers=headers()).status_code == 204
    assert calls == [("tenant-1", "user-1", "m-1", -1, "outdated")]

    monkeypatch.setattr(api.usage_log, "set_feedback", lambda *args: False)
    assert client.post("/api/feedback", json=body, headers=headers()).status_code == 404

    def boom(*args):
        raise OSError("down")

    monkeypatch.setattr(api.usage_log, "set_feedback", boom)
    assert client.post("/api/feedback", json=body, headers=headers()).status_code == 503


@pytest.mark.parametrize("body", [
    {"message_id": "m-1", "rating": 2},
    {"message_id": "m-1", "rating": -1, "reason": "因为我不喜欢"},
    {"message_id": "", "rating": 1},
])
def test_feedback_rejects_bad_input(client: TestClient, monkeypatch: pytest.MonkeyPatch, body: dict) -> None:
    _history_on(monkeypatch)
    assert client.post("/api/feedback", json=body, headers=headers()).status_code == 422


def test_usage_report_is_admin_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get("/api/admin/usage", headers=headers()).status_code == 403
    assert client.get("/api/admin/usage", headers=headers("STAFF")).status_code == 403
    assert client.get("/api/admin/usage", headers=headers("ADMIN")).status_code == 503  # 没配数据库

    from datetime import datetime

    _history_on(monkeypatch)
    seen: list[tuple] = []
    rows = [{"question": "缓考怎么申请", "outcome": "refused", "guest": False, "at": datetime(2026, 10, 27, 9, 0),
             "rating": None, "reason": "", "answer": ""}]
    monkeypatch.setattr(api.usage_log, "usage_rows", lambda tenant, days: seen.append((tenant, days)) or rows)
    monkeypatch.setattr(api, "read_audit", lambda tenant: [])

    response = client.get("/api/admin/usage?days=30", headers=headers("ADMIN"))

    assert response.status_code == 200
    assert seen == [("tenant-1", 30)]
    report = response.json()
    assert report["days"] == 30
    assert report["totals"]["refused"] == 1
    assert report["unanswered"][0]["question"] == "缓考怎么申请"

    assert client.get("/api/admin/usage?days=0", headers=headers("ADMIN")).status_code == 422
    assert client.get("/api/admin/usage?days=365", headers=headers("ADMIN")).status_code == 422


def test_session_messages_carry_ids_and_the_students_own_rating(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _history_on(monkeypatch)
    stored = [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "在", "id": "m-7", "feedback": -1, "feedback_reason": "wrong"},
    ]
    monkeypatch.setattr(api.chat_history, "load_messages", lambda t, u, s: stored)

    body = client.get("/api/sessions/s1/messages", headers=headers()).json()

    assert body[0]["id"] == "" and body[0]["feedback"] == 0
    assert body[1]["id"] == "m-7" and body[1]["feedback"] == -1 and body[1]["feedback_reason"] == "wrong"


def test_academic_sandbox_and_conflict_endpoints(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import course_planner
    import jw_account
    from workspace import SchoolAi

    monkeypatch.setattr(course_planner, "school_ai", lambda tid: SchoolAi(jw_mode="demo"))
    monkeypatch.setattr(jw_account, "school_ai", lambda tid: SchoolAi(jw_mode="demo"))

    # 访客挂件不可访问学业规划接口
    assert client.get("/api/academic/sandbox", headers=headers("GUEST")).status_code == 403

    res = client.get("/api/academic/sandbox?day=2026-10-12", headers=headers())
    assert res.status_code == 200
    data = res.json()
    assert data["bound"] is True and data["summary"]["total_missing"] == 44.5

    check = client.post(
        "/api/academic/check-conflicts",
        headers=headers(),
        json={"candidates": [{"name": "美术鉴赏", "weekday": 3, "periods": "1-2节", "weeks": "9-16周"}]},
    )
    assert check.status_code == 200
    assert check.json()["has_any_conflict"] is False


# ---------- Phase 2/3: HITL 工单、个人伴学空间与企微/QQ IM API ----------


def test_ticket_endpoints_permissions_and_flow(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import tickets
    from datetime import datetime, timezone

    # 未开 Postgres 时优雅返回 503
    monkeypatch.setattr(tickets, "enabled", lambda: False)
    assert client.post("/api/tickets", json={"question": "转专业要求"}, headers=headers()).status_code == 503

    monkeypatch.setattr(tickets, "enabled", lambda: True)
    now = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
    sample = tickets.Ticket(
        id="tk-1",
        tenant_id="tenant-1",
        user_id="user-1",
        message_id="m-9",
        category="academic",
        question="跨院选课上限是多少？",
        context_summary="原 AI 回答未查到具体学分上限",
        status="open",
        reply="",
        replied_by_id=None,
        replied_at=None,
        merged_at=None,
        created_at=now,
        updated_at=now,
        requester_name="张三",
        requester_student_no="20260001",
    )
    monkeypatch.setattr(tickets, "create_ticket", lambda *a, **kw: sample)
    monkeypatch.setattr(tickets, "list_user_tickets", lambda tid, uid: [sample])
    monkeypatch.setattr(tickets, "list_tenant_tickets", lambda tid, status="": [sample])

    # 访客不能提工单；普通学生不能访问教工管理端点
    assert client.post("/api/tickets", json={"question": "test"}, headers=headers("GUEST")).status_code == 403
    assert client.get("/api/admin/tickets", headers=headers("STUDENT")).status_code == 403

    created = client.post("/api/tickets", json={"question": "跨院选课上限是多少？", "category": "academic"}, headers=headers())
    assert created.status_code == 200 and created.json()["id"] == "tk-1"
    assert len(client.get("/api/tickets", headers=headers()).json()) == 1
    assert len(client.get("/api/admin/tickets", headers=headers("STAFF")).json()) == 1

    merged_ticket = tickets.Ticket(**{**sample.__dict__, "status": "merged", "reply": "每学期最多跨院选 6 学分。", "merged_at": now})
    monkeypatch.setattr(tickets, "reply_ticket", lambda *a, **kw: merged_ticket)
    monkeypatch.setattr(tickets, "merge_ticket_to_kb", lambda *a, **kw: (merged_ticket, DocumentInfo(name="工单答疑_tk-1.md", chars=60, indexed=False, source="uploaded")))

    rep = client.post("/api/admin/tickets/tk-1/reply", json={"reply": "每学期最多跨院选 6 学分。"}, headers=headers("STAFF"))
    assert rep.status_code == 200 and rep.json()["reply"] == "每学期最多跨院选 6 学分。"

    mrg = client.post("/api/admin/tickets/tk-1/merge", json={"title": "跨院选课学分上限"}, headers=headers("STAFF"))
    assert mrg.status_code == 200
    assert mrg.json()["ticket"]["status"] == "merged"
    assert mrg.json()["document"] == "工单答疑_tk-1.md"


def test_study_space_endpoints_pptx_flashcards_and_quiz(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import study_space
    from pptx import Presentation

    monkeypatch.setattr(study_space, "enabled", lambda: False)
    assert client.get("/api/study/courses", headers=headers()).status_code == 503

    monkeypatch.setattr(study_space, "enabled", lambda: True)
    assert client.get("/api/study/courses", headers=headers("GUEST")).status_code == 403

    course = {"id": "c1", "name": "算法设计与分析", "code": "CS301", "teacher": "刘教授", "term": "2026-1", "source": "manual", "created_at": "", "updated_at": "", "material_count": 0, "char_count": 0, "card_count": 0, "due_card_count": 0}
    monkeypatch.setattr(study_space, "list_courses", lambda t, u: [course])
    monkeypatch.setattr(study_space, "create_course", lambda *a, **kw: course)
    monkeypatch.setattr(study_space, "add_file_material", lambda t, u, cid, fname, data: {"id": "m1", "course_id": cid, "title": "课件", "kind": "file", "filename": fname, "char_count": len(data), "chunk_count": 2, "summary": "动态规划", "created_at": ""})
    monkeypatch.setattr(study_space, "generate_flashcards", lambda *a, **kw: [{"id": "k1", "course_id": "c1", "material_id": "", "front": "DP 无后效性", "back": "未来状态只依赖当前状态", "tags": "核心", "reps": 0, "interval_days": 0, "ease": 2.5, "due_at": "", "last_reviewed_at": None, "created_at": ""}])
    monkeypatch.setattr(study_space, "generate_quiz", lambda *a, **kw: {"quiz_id": "q1", "course_id": "c1", "course_name": "算法设计与分析", "questions": [{"id": "q1", "type": "choice", "question": "DP核心?", "options": ["A. 贪心", "B. 最优子结构"]}]})
    monkeypatch.setattr(study_space, "grade_quiz", lambda *a, **kw: {"quiz_id": "q1", "course_id": "c1", "course_name": "算法设计与分析", "score": 10, "max_score": 10, "percent": 100, "results": []})

    assert client.get("/api/study/courses", headers=headers()).status_code == 200
    assert client.post("/api/study/courses", json={"name": "算法设计与分析"}, headers=headers()).status_code == 200

    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[1])
    s1.shapes.title.text = "动态规划"
    buf = io.BytesIO()
    prs.save(buf)
    up = client.post(
        "/api/study/courses/c1/materials/file",
        files={"file": ("dp.pptx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        headers=headers(),
    )
    assert up.status_code == 200 and up.json()["filename"] == "dp.pptx"

    fc = client.post("/api/study/courses/c1/flashcards/generate", json={"count": 3}, headers=headers())
    assert fc.status_code == 200 and fc.json()[0]["id"] == "k1"

    qz = client.post("/api/study/courses/c1/quiz", json={"count": 1}, headers=headers())
    assert qz.status_code == 200 and qz.json()["quiz_id"] == "q1"

    gr = client.post("/api/study/quizzes/q1/grade", json={"answers": {"q1": "B"}}, headers=headers())
    assert gr.status_code == 200 and gr.json()["percent"] == 100


def test_im_endpoints_and_qq_onebot_webhook_signature(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib
    import hmac
    import im_gateway
    import im_scheduler
    import qq_bot
    from workspace import SchoolIm

    _history_on(monkeypatch)
    monkeypatch.setattr(api, "school_im", lambda tid: SchoolIm(wecom_bot_id="wb-1", wecom_bot_secret="sec", qq_onebot_url="http://127.0.0.1:5700", qq_onebot_token="qq-secret"))
    monkeypatch.setattr(api, "list_configured_im_tenants", lambda: [("tenant-1", SchoolIm(qq_onebot_url="http://127.0.0.1:5700", qq_onebot_token="qq-secret"))])
    monkeypatch.setattr(im_gateway, "create_bind_code", lambda t, u: "654321")
    monkeypatch.setattr(im_gateway, "list_user_bindings", lambda t, u: [{"id": "b1", "platform": "qq", "external_id": "10001"}])
    monkeypatch.setattr(im_scheduler, "build_morning_digest", lambda t, u, d, kw="": f"早报({t}):{kw}")

    bindings = client.get("/api/im/bindings", headers=headers())
    assert bindings.status_code == 200
    assert bindings.json()["wecom_enabled"] is True and bindings.json()["qq_enabled"] is True

    code = client.post("/api/im/bind-code", headers=headers())
    assert code.status_code == 200 and code.json()["code"] == "654321"

    preview = client.get("/api/im/digest-preview?keywords=%E5%A5%96%E5%AD%A6%E9%87%91", headers=headers())
    assert preview.status_code == 200 and preview.json()["content"] == "早报(tenant-1):奖学金"

    # QQ OneBot Webhook 验签：伪造签名 401，正确 HMAC-SHA1 放行并回复
    raw_payload = json.dumps({
        "post_type": "message",
        "message_type": "private",
        "self_id": 99999,
        "user_id": 10001,
        "message": "帮助",
    }).encode("utf-8")

    bad_sig = client.post("/api/im/qq/event/tenant-1", content=raw_payload, headers={"X-Signature": "sha1=0000"})
    assert bad_sig.status_code == 401

    good_hmac = hmac.new(b"qq-secret", raw_payload, hashlib.sha1).hexdigest()
    sent_calls: list[tuple] = []
    monkeypatch.setattr(im_gateway, "handle_incoming_message", lambda *a, **kw: "IM 助手已回复")
    monkeypatch.setattr(qq_bot, "send_private_message", lambda url, tok, uid, txt: sent_calls.append((url, tok, uid, txt)) or True)

    ok_resp = client.post("/api/im/qq/event", content=raw_payload, headers={"X-Signature": f"sha1={good_hmac}"})
    assert ok_resp.status_code == 200
    assert ok_resp.json() == {"ok": True, "delivered": True}
    assert sent_calls == [("http://127.0.0.1:5700", "qq-secret", "10001", "IM 助手已回复")]
