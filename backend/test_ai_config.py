"""学校的 AI 服务配置：密文解密、选用优先级、没配模型时的问答链路、状态与连通测试接口。

读库、模型全部打桩，不需要 Postgres / Redis / API Key:
    cd backend && pytest test_ai_config.py
"""
from __future__ import annotations

import time
from contextvars import copy_context
from unittest.mock import MagicMock

import ai_config
import api
import graph_app
import jwt
import pytest
import secret_box
import workspace
from auth import JWT_ALGORITHM, JWT_ISSUER
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from llm_request import LlmRequest, pop_llm_request, push_llm_request, stream_with_llm_request
from llm_service import NOT_CONFIGURED
from workspace import SchoolAi

SECRET = "test-secret-do-not-use-in-production"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    for name in (
        "DATABASE_URL", "deepseek_API_key", "deepseek_url", "deepseek_model",
        "kimi_API_key", "kimi_url", "zhi_pu_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(graph_app, "_delta_emitter", lambda: (lambda text: None))
    workspace.clear_cache()
    yield
    workspace.clear_cache()


def _school(monkeypatch: pytest.MonkeyPatch, **fields: str) -> None:
    monkeypatch.setattr(ai_config, "school_ai", lambda tenant_id: SchoolAi(**fields))


# ---------- 密文 ----------


def test_secret_box_round_trip() -> None:
    token = secret_box.encrypt("sk-校园-123")
    assert token.startswith("v1.")
    assert "sk-" not in token
    assert secret_box.decrypt(token) == "sk-校园-123"
    assert secret_box.decrypt("") == ""


def test_secret_box_rejects_tampering_and_other_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    token = secret_box.encrypt("sk-abc")
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises((InvalidTag, ValueError)):
        secret_box.decrypt(tampered)
    monkeypatch.setenv("AUTH_JWT_SECRET", "another-secret")
    with pytest.raises(InvalidTag):
        secret_box.decrypt(token)


def test_school_ai_decrypts_row_and_drops_bad_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    good = secret_box.encrypt("sk-school")
    map_key = secret_box.encrypt("amap-key")
    monkeypatch.setattr(
        workspace, "_fetch_one",
        lambda sql, params: (
            " https://api.x.com/v1 ", "m1", good, "", "", "v1.broken",
            map_key, "", "zhengfang", "https://cas.x.edu.cn:90/cas/", " https://jw.x.edu.cn/jwglxt ",
            "https://www.x.edu.cn/a.htm\n\n https://jwc.x.edu.cn/b.htm ",
        ),
    )
    school = workspace.school_ai("t1")
    assert school == SchoolAi(
        "https://api.x.com/v1", "m1", "sk-school", "", "", "",
        map_key="amap-key", jw_mode="zhengfang",
        jw_cas_url="https://cas.x.edu.cn:90/cas", jw_base_url="https://jw.x.edu.cn/jwglxt",
        news_urls=("https://www.x.edu.cn/a.htm", "https://jwc.x.edu.cn/b.htm"),
    )


# ---------- 选用优先级 ----------


def test_nothing_configured_returns_none() -> None:
    assert ai_config.chat_endpoint("t1") is None
    assert ai_config.reader_endpoint("t1") is None


def test_platform_env_is_last_resort_and_placeholders_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("deepseek_API_key", "your_deepseek_api_key")
    assert ai_config.chat_endpoint("t1") is None

    monkeypatch.setenv("deepseek_API_key", "sk-platform")
    monkeypatch.setenv("kimi_API_key", "sk-kimi")
    monkeypatch.setenv("kimi_url", "https://api.moonshot.cn/v1/chat/completions")
    chat = ai_config.chat_endpoint("t1")
    reader = ai_config.reader_endpoint("t1")
    assert (chat.source, chat.api_key, chat.model) == ("platform", "sk-platform", "deepseek-chat")
    assert reader.base_url == "https://api.moonshot.cn/v1"


def test_school_beats_platform_and_personal_beats_school(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("deepseek_API_key", "sk-platform")
    _school(monkeypatch, chat_key="sk-school", chat_model="qwen-plus", chat_base_url="https://dashscope.example/v1/")
    school = ai_config.chat_endpoint("t1")
    assert (school.source, school.model, school.base_url) == ("school", "qwen-plus", "https://dashscope.example/v1")

    token = push_llm_request(LlmRequest("sk-mine", "https://api.deepseek.com/v1", "deepseek-chat", stream=False))
    try:
        mine = ai_config.chat_endpoint("t1")
        assert (mine.source, mine.api_key, mine.stream) == ("personal", "sk-mine", False)
        # 状态页与连通测试看的是学校这一层，不受个人密钥影响
        assert ai_config.school_chat_endpoint("t1").source == "school"
    finally:
        pop_llm_request(token)


def test_personal_key_survives_every_step_of_a_stream() -> None:
    """StreamingResponse 的取法：每次 next() 都在一份新的上下文副本里。"""
    mine = LlmRequest("sk-mine", "https://api.deepseek.com/v1", "deepseek-chat")

    def steps():
        for _ in range(3):
            yield ai_config.current_llm_request()

    stream = stream_with_llm_request(mine, steps())
    seen = []
    while True:
        try:
            seen.append(copy_context().run(next, stream))
        except StopIteration:
            break
    assert seen == [mine, mine, mine]
    assert ai_config.current_llm_request() is None


def test_scrub_hides_key() -> None:
    endpoint = ai_config.ModelEndpoint("sk-secret", "https://x", "m", "school")
    assert ai_config.scrub("bad key sk-secret", endpoint) == "bad key ***"


# ---------- 问答链路 ----------


def _real_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_app, "get_deepseek", lambda: graph_app.DeepSeekService(use_mock=False))
    monkeypatch.setattr(graph_app, "get_kimi", lambda: graph_app.KimiExtractService(use_mock=False))
    monkeypatch.setattr(graph_app, "workspace_settings", lambda tenant_id: workspace.DEFAULT_SETTINGS)


def test_unconfigured_chitchat_says_so_instead_of_faking(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_services(monkeypatch)
    out = graph_app.generate_node(
        {"user_question": "你好", "intent": "DIRECT", "context": "", "tenant_id": "t1", "messages": []}
    )
    assert out["final_answer"] == NOT_CONFIGURED


def test_unconfigured_rag_answer_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_services(monkeypatch)
    out = graph_app.generate_node(
        {"user_question": "门禁", "intent": "RAG", "context": "资料", "tenant_id": "t1", "messages": []}
    )
    assert out["final_answer"] == NOT_CONFIGURED


def test_unconfigured_intent_falls_back_to_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_services(monkeypatch)
    assert graph_app.intent_node({"user_question": "补考什么时候", "tenant_id": "t1"})["intent"] == "RAG"
    assert graph_app.intent_node({"user_question": "你好呀", "tenant_id": "t1"})["intent"] == "DIRECT"


def test_unconfigured_emotion_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_services(monkeypatch)
    out = graph_app.emotion_node({"user_question": "好累", "tenant_id": "t1", "user_id": ""})
    assert out["emotion_data"] == {}


def test_reader_without_model_falls_back_to_vector_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """没配精读模型时不能再拿模拟摘录当参考资料。"""
    _real_services(monkeypatch)
    vdb = MagicMock()
    vdb.search_chunks.return_value = []
    vdb.format_context.return_value = "向量片段"
    monkeypatch.setattr(graph_app, "get_vector_db", lambda: vdb)

    out = graph_app.retrieve_node({
        "user_question": "门禁", "route_strategy": "auto",
        "current_doc_content": "--- 文档来源: a.md ---\n门禁 22 点", "tenant_id": "t1",
    })

    assert out["context"] == "向量片段"
    vdb.search_chunks.assert_called_once()


def test_school_model_is_used_for_chitchat(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_services(monkeypatch)
    _school(monkeypatch, chat_key="sk-school", chat_model="qwen-plus")
    calls: list[tuple[str, str]] = []

    def fake_stream(self, messages, endpoint, temperature=0.8):
        calls.append((endpoint.source, endpoint.model))
        yield "你好"

    monkeypatch.setattr(graph_app.DeepSeekService, "stream_chat", fake_stream)
    out = graph_app.generate_node(
        {"user_question": "你好", "intent": "DIRECT", "context": "", "tenant_id": "t1", "messages": []}
    )
    assert out["final_answer"] == "你好"
    assert calls == [("school", "qwen-plus")]


def test_chitchat_failure_names_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_services(monkeypatch)
    _school(monkeypatch, chat_key="sk-school")

    def broken(self, messages, endpoint, temperature=0.8):
        raise RuntimeError("Error code: 401 sk-school")
        yield ""

    monkeypatch.setattr(graph_app.DeepSeekService, "stream_chat", broken)
    out = graph_app.generate_node(
        {"user_question": "你好", "intent": "DIRECT", "context": "", "tenant_id": "t1", "messages": []}
    )
    assert out["final_answer"].startswith("抱歉，我暂时无法回答这个问题。错误信息:（学校配置）")
    assert "sk-school" not in out["final_answer"]


# ---------- 接口 ----------


def headers(role: str = "STUDENT") -> dict[str, str]:
    now = int(time.time())
    claims = {
        "sub": "user-1", "tid": "tenant-1", "sno": "20260001", "name": "张三",
        "role": role, "iss": JWT_ISSUER, "iat": now, "exp": now + 3600,
    }
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm=JWT_ALGORITHM)}"}


def test_status_reports_sources_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _school(monkeypatch, chat_key="sk-school", chat_model="qwen-plus")
    monkeypatch.setenv("zhi_pu_API_KEY", "zp-key")
    body = TestClient(api.app).get("/api/ai/status", headers=headers()).json()

    assert body["chat"] == {"configured": True, "source": "school", "model": "qwen-plus"}
    assert body["reader"] == {"configured": False, "source": None, "model": ""}
    assert body["embedding"]["configured"] is True
    assert "sk-school" not in str(body)


def test_only_admin_can_run_connection_test() -> None:
    response = TestClient(api.app).post("/api/ai/test", json={"service": "chat"}, headers=headers("STAFF"))
    assert response.status_code == 403


def test_connection_test_reports_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    response = TestClient(api.app).post("/api/ai/test", json={"service": "chat"}, headers=headers("ADMIN"))
    assert response.status_code == 400
    assert "还没配置" in response.json()["detail"]


def test_connection_test_hides_key_in_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _school(monkeypatch, chat_key="sk-school")
    fake = MagicMock()
    fake.chat.completions.create.side_effect = RuntimeError("Incorrect API key sk-school")
    monkeypatch.setattr(api, "client_for", lambda endpoint, timeout, retries: fake)

    response = TestClient(api.app).post("/api/ai/test", json={"service": "chat"}, headers=headers("ADMIN"))
    assert response.status_code == 502
    assert "sk-school" not in response.json()["detail"]
    assert "学校配置" in response.json()["detail"]
