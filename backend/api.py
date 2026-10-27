"""FastAPI 层：把 LangGraph 对话与知识库能力暴露为 HTTP/SSE 接口，供独立前端调用。

启动方式:
    uvicorn api:app --reload --port 8001     # 在 backend/ 目录下执行；8001 避开本机已占用的 8000
"""
from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any, Literal

import chat_history
import desk_log
import httpx
import jw_account
import memory_store
import outbound
import school_tools
import session_summary
import today_card
import usage_log
import usage_stats
from agent import read_audit, tool_labels
from ai_config import (
    READER_TIMEOUT_SECONDS,
    ModelEndpoint,
    ToolKey,
    chat_endpoint,
    client_for,
    embedding_spec,
    map_key,
    reader_endpoint,
    school_chat_endpoint,
    scrub,
    search_key,
)
from auth import (
    AdminDep,
    CurrentUser,
    CurrentUserDep,
    KnowledgeManagerDep,
    MemberDep,
    jwt_secret,
    scoped_thread_id,
)
from chat_stream import sse, sse_from_graph_stream
from env_config import load_backend_env
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from graph_app import MAX_STORED_TURNS, build_graph, get_vector_db, refusal_text
from jw_zhengfang import JwAuthError, JwCaptcha, JwError
from knowledge import knowledge_base
from llm_request import LlmRequest, stream_with_llm_request
from llm_service import FAILED_PREFIX, NOT_CONFIGURED, RAG_FAILED_PREFIX
from logging_config import get_logger, setup_logging
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator
from rate_limit import enforce_chat_quota, enforce_embed_quota
from web_import import UnsafeUrlError
from workspace import embed_settings, forget_school_ai, school_ai, school_name, workspace_settings

logger = get_logger(__name__)

load_backend_env()

# 检索子路由交给 graph_app 按文档长度自动判定
ROUTE_STRATEGY = "auto"

# 上传体积上限。校规手册这类文档远小于此，超出的多半是误传或恶意请求。
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
_UPLOAD_READ_CHUNK = 1024 * 1024

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    # 密钥缺失时宁可起不来，也不要让所有接口裸奔
    jwt_secret()
    knowledge_base.autoload()
    yield


app = FastAPI(title="智悦校园 API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    # 收敛到实际用到的方法与请求头。配合 allow_credentials 时，
    # 通配会把任意跨站页面能发的请求面放到最大。
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


# ============================================================
# 请求 / 响应模型
# ============================================================

class LlmConfig(BaseModel):
    """浏览器带来的模型配置。只用于这一次调用，不写入服务器。"""

    api_key: str = Field(default="", max_length=300)
    base_url: str = Field(default="https://api.deepseek.com/v1", max_length=200)
    model: str = Field(default="deepseek-chat", max_length=80)
    stream: bool = True

    @field_validator("base_url")
    @classmethod
    def https_only(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if cleaned and not cleaned.startswith("https://"):
            raise ValueError("模型地址必须是 https")
        return cleaned


class AiServiceTest(BaseModel):
    service: Literal["chat", "reader", "embedding", "map", "search", "jw", "news"]


class JwBind(BaseModel):
    username: str = Field(..., min_length=1, max_length=40)
    password: str = Field(..., min_length=1, max_length=100)
    captcha: str = Field(default="", max_length=10)


class ChatRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    llm: LlmConfig | None = None
    # 重新生成 / 改了再问：最后一轮的原提问。和服务端记录的最后一轮对得上才会先删掉那一轮
    replace_question: str | None = None


class Message(BaseModel):
    # 入库的消息才有编号；没有编号的（访客、Redis 里补来的）不能打分
    id: str = ""
    role: str
    content: str
    context: str = ""
    sources: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    emotion: dict[str, Any] | None = None
    safety: dict[str, Any] | None = None
    # 这个人自己打的分：1 赞、-1 踩、0 没打
    feedback: int = 0
    feedback_reason: str = ""


class FeedbackRequest(BaseModel):
    message_id: str = Field(..., min_length=1, max_length=64)
    rating: Literal[-1, 0, 1]
    reason: Literal["", "wrong", "outdated", "verbose", "other"] = ""


class SessionSummary(BaseModel):
    thread_id: str
    title: str
    updated_at: str


class MemoryResponse(BaseModel):
    id: str
    content: str
    created_at: float
    origin: str


class MemoryCreate(BaseModel):
    contents: list[str] = Field(..., min_length=1, max_length=memory_store.MAX_MEMORIES)


class UrlImport(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)


class SessionResponse(BaseModel):
    thread_id: str


class DocumentResponse(BaseModel):
    name: str
    chars: int
    indexed: bool
    source: str


@app.get("/api/health")
def health() -> dict[str, str]:
    """探活接口，容器 healthcheck 会调，故不要求令牌。"""
    return {"status": "ok"}


@app.get("/api/workspace")
def get_workspace(user: CurrentUserDep) -> dict[str, Any]:
    """前端展示用：助手称呼与是否只按资料回答。完整设置只在 Next 的管理接口里可改。"""
    settings = workspace_settings(user.tenant_id)
    return {"assistant_name": settings.assistant_name, "query_only": settings.query_only}


@app.post("/api/llm/ping")
def ping_llm(payload: LlmConfig, _: MemberDep) -> dict[str, Any]:
    """用浏览器里的密钥试一次连通，不把密钥存下来。"""
    request = _as_llm_request(payload)
    if request is None:
        raise HTTPException(status_code=400, detail="请先填写 API Key")
    try:
        client = OpenAI(api_key=request.api_key, base_url=request.base_url, timeout=20)
        listed = client.models.list()
        names = [item.id for item in list(listed)[:8]]
    except Exception as exc:
        detail = str(exc)
        if payload.api_key and payload.api_key in detail:
            detail = "密钥或地址不被接受"
        raise HTTPException(status_code=502, detail=f"连通失败: {detail}") from exc
    return {"ok": True, "models": names}


def _endpoint_status(endpoint: ModelEndpoint | None) -> dict[str, Any]:
    if endpoint is None:
        return {"configured": False, "source": None, "model": ""}
    return {"configured": True, "source": endpoint.source, "model": endpoint.model}


@app.get("/api/ai/status")
def ai_status(user: MemberDep) -> dict[str, Any]:
    """本校各项 AI 服务配没配、用的是学校的还是平台的。不含任何密钥，学生也能看。"""
    school = school_ai(user.tenant_id)
    spec = embedding_spec(user.tenant_id)
    return {
        "chat": _endpoint_status(school_chat_endpoint(user.tenant_id)),
        "reader": _endpoint_status(reader_endpoint(user.tenant_id)),
        "embedding": {
            "configured": spec is not None,
            "source": spec.source if spec else None,
            "model": spec.model if spec else "",
        },
        "map": _key_status(map_key(user.tenant_id), "高德 Web 服务"),
        "search": _key_status(search_key(user.tenant_id), "博查 Web Search"),
        "jw": {"configured": school.jw_mode != "off", "source": "school" if school.jw_mode != "off" else None, "model": school.jw_mode},
        "news": {"configured": bool(school.news_urls), "source": "school" if school.news_urls else None, "model": f"{len(school.news_urls)} 个网址"},
    }


def _key_status(key: ToolKey | None, name: str) -> dict[str, Any]:
    return {"configured": key is not None, "source": key.source if key else None, "model": name}


def _test_tool(service: str, tenant_id: str) -> dict[str, Any]:
    """地图、搜索、教务、通知的连通测试：都是真调一次最便宜的接口。"""
    school = school_ai(tenant_id)
    try:
        if service == "map":
            key = map_key(tenant_id)
            if key is None:
                raise HTTPException(status_code=400, detail="还没配置：先填好高德 Key 并保存")
            places = school_tools.find_places(key.key, school_name(tenant_id), "图书馆", 1)
            return {"ok": True, "source": key.source, "model": f"找到「{places[0].name}」" if places else "Key 可用，没搜到图书馆"}
        if service == "search":
            key = search_key(tenant_id)
            if key is None:
                raise HTTPException(status_code=400, detail="还没配置：先填好博查 Key 并保存")
            results = school_tools.web_search(key.key, school_name(tenant_id) or "大学", 1)
            return {"ok": True, "source": key.source, "model": f"返回 {len(results)} 条结果"}
        if service == "news":
            items = school_tools.school_news(school.news_urls)
            return {"ok": True, "source": "school", "model": f"最新：{items[0]['title'][:24]}" if items else "没解析到文章"}
        if school.jw_mode == "off":
            raise HTTPException(status_code=400, detail="教务查询是关闭的")
        if school.jw_mode == "zhengfang":
            jw_account.probe(tenant_id)
        return {"ok": True, "source": "school", "model": "演示数据" if school.jw_mode == "demo" else "统一身份认证可访问"}
    except (school_tools.ToolError, JwError) as exc:
        raise HTTPException(status_code=502, detail=f"连通失败: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"连通失败: 网络错误 {type(exc).__name__}") from exc
    except outbound.UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=f"连通失败: {exc}") from exc
    except ValueError as exc:
        # 对方返回的不是预期的 JSON（比如维护页）
        raise HTTPException(status_code=502, detail="连通失败: 对方返回的内容无法解析") from exc


@app.post("/api/ai/test")
def test_ai_service(payload: AiServiceTest, user: AdminDep) -> dict[str, Any]:
    """用本校当前生效的配置真实调一次。管理员刚保存完就会点它，所以先丢掉缓存。"""
    forget_school_ai(user.tenant_id)
    if payload.service in ("map", "search", "jw", "news"):
        return _test_tool(payload.service, user.tenant_id)
    if payload.service == "embedding":
        spec = embedding_spec(user.tenant_id)
        if spec is None:
            raise HTTPException(status_code=400, detail="还没配置智谱向量化密钥。请管理员在「AI 服务」里填写，或在服务器 .env 配置 zhi_pu_API_KEY")
        vdb = get_vector_db(user.tenant_id)
        if vdb is None:
            raise HTTPException(status_code=502, detail="向量库初始化失败，查看后端日志")
        try:
            vdb.embedding_model.embed_query("连通测试")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"连通失败: {exc}") from exc
        return {"ok": True, "source": spec.source, "model": spec.model}

    is_chat = payload.service == "chat"
    endpoint = school_chat_endpoint(user.tenant_id) if is_chat else reader_endpoint(user.tenant_id)
    if endpoint is None:
        raise HTTPException(status_code=400, detail="还没配置：先填好密钥并保存")
    timeout = 20.0 if is_chat else min(READER_TIMEOUT_SECONDS, 30.0)
    try:
        # 只要 1 个 token：既验密钥，也验模型 ID 写没写对
        client_for(endpoint, timeout=timeout, retries=0).chat.completions.create(
            model=endpoint.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"连通失败（{endpoint.label}）: {scrub(str(exc), endpoint)}"
        ) from exc
    return {"ok": True, "source": endpoint.source, "model": endpoint.model}


# ============================================================
# 教务账号绑定（只存登录后的 Cookie，不存密码）
# ============================================================

@app.get("/api/jw/account")
def get_jw_account(user: MemberDep) -> dict[str, Any]:
    return jw_account.status(user.tenant_id, user.id)


@app.post("/api/jw/account")
def bind_jw_account(payload: JwBind, user: MemberDep) -> dict[str, Any]:
    """需要验证码时返回 {"captcha": 图片 base64}，学生填好后连同账号密码再提交一次。"""
    try:
        return jw_account.bind(user.tenant_id, user.id, payload.username.strip(), payload.password, payload.captcha.strip())
    except JwCaptcha as exc:
        return {"captcha": exc.image_base64}
    except JW_ERRORS as exc:
        raise _jw_http_error(exc) from exc


@app.post("/api/jw/account/wecom")
def start_jw_wecom(user: MemberDep) -> dict[str, Any]:
    """开一张企业微信登录二维码：{"qr": PNG base64, "expiresIn": 秒}。"""
    try:
        return jw_account.wecom_start(user.tenant_id, user.id)
    except JW_ERRORS as exc:
        raise _jw_http_error(exc) from exc


@app.get("/api/jw/account/wecom")
def poll_jw_wecom(user: MemberDep, id: str = Query(..., max_length=64)) -> dict[str, Any]:
    """长轮询这张二维码的扫码结果（最多挂 30 秒），没到 bound / expired 前端就接着问。"""
    try:
        return jw_account.wecom_poll(user.tenant_id, user.id, id)
    except JW_ERRORS as exc:
        raise _jw_http_error(exc) from exc


JW_ERRORS = (JwError, httpx.HTTPError, ValueError)


def _jw_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, JwAuthError):
        # 不用 401：前端会把 401 当成本站登录过期
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, JwError):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, httpx.HTTPError):
        return HTTPException(status_code=502, detail="连不上学校的统一身份认证，请稍后再试")
    if isinstance(exc, outbound.UnsafeUrlError):
        return HTTPException(status_code=400, detail=f"学校的教务地址无法访问（{exc}），请联系管理员")
    return HTTPException(status_code=502, detail="学校的统一身份认证返回异常，请稍后再试")


@app.delete("/api/jw/account")
def unbind_jw_account(user: MemberDep) -> dict[str, Any]:
    jw_account.unbind(user.tenant_id, user.id)
    return jw_account.status(user.tenant_id, user.id)


@app.get("/api/today")
def get_today_card(
    user: MemberDep,
    day: str | None = Query(None, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, Any]:
    """今天的课和临近的考试。day 是学生本机的日期，只在服务器日期前后一天内才采信。"""
    try:
        return today_card.build_card(user.tenant_id, user.id, today_card.clamp_day(day, date.today()))
    except JW_ERRORS as exc:
        raise _jw_http_error(exc) from exc


# ============================================================
# 会话
# ============================================================

@app.post("/api/sessions", response_model=SessionResponse)
def create_session(_: CurrentUserDep) -> SessionResponse:
    return SessionResponse(thread_id=str(uuid.uuid4()))


def _uses_history_db(user: CurrentUser) -> bool:
    """访客（嵌入挂件）的对话只在 Redis 里，登录用户的才进库。"""
    return not user.is_guest and chat_history.enabled()


def _is_failed_answer(content: str) -> bool:
    return not content or content.startswith((FAILED_PREFIX, RAG_FAILED_PREFIX)) or content == NOT_CONFIGURED


def _successful_turns(stored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Redis 快照里成对的「提问 + 成功回答」，失败的那一轮整轮丢掉。"""
    kept: list[dict[str, Any]] = []
    for question, answer in zip(stored, stored[1:]):
        if question.get("role") == "user" and answer.get("role") == "assistant" and not _is_failed_answer(answer.get("content", "")):
            kept.extend([question, answer])
    return kept


def _to_message(item: dict[str, Any]) -> Message:
    return Message(
        id=item.get("id", ""),
        feedback=item.get("feedback", 0),
        feedback_reason=item.get("feedback_reason", ""),
        role=item.get("role", ""),
        content=item.get("content", ""),
        context=item.get("context", "") or "",
        sources=item.get("sources") or [],
        tables=item.get("tables") or [],
        emotion=item.get("emotion"),
        safety=item.get("safety"),
    )


def _redis_messages(user: CurrentUser, thread_id: str) -> list[dict[str, Any]]:
    try:
        snapshot = build_graph().get_state({"configurable": {"thread_id": scoped_thread_id(user, thread_id)}})
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"无法读取会话历史（检查 Redis）: {exc}") from exc
    stored = snapshot.values.get("messages") if snapshot and snapshot.values else None
    return [
        item for item in stored or []
        if item.get("role") in ("user", "assistant") and item.get("content")
    ]


@app.get("/api/sessions", response_model=list[SessionSummary])
def list_sessions(
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=chat_history.LIST_MAX_LIMIT)] = chat_history.LIST_DEFAULT_LIMIT,
) -> list[SessionSummary]:
    if not _uses_history_db(user):
        return []
    try:
        sessions = chat_history.list_sessions(user.tenant_id, user.id, limit)
    except Exception as exc:
        logger.warning(f"[History] 会话列表读取失败: {exc}")
        raise HTTPException(status_code=503, detail="会话列表暂时读不到，请稍后再试") from exc
    return [
        SessionSummary(thread_id=s.thread_id, title=s.title, updated_at=s.updated_at.isoformat())
        for s in sessions
    ]


@app.delete("/api/sessions/{thread_id}", status_code=204)
def delete_session(thread_id: str, user: MemberDep) -> None:
    if chat_history.enabled():
        try:
            found = chat_history.delete_session(user.tenant_id, user.id, thread_id)
        except Exception as exc:
            logger.warning(f"[History] 删除会话失败: {exc}")
            raise HTTPException(status_code=503, detail="暂时删不掉，请稍后再试") from exc
        if not found:
            raise HTTPException(status_code=404, detail="没有这个会话")
    try:
        build_graph().checkpointer.delete_thread(scoped_thread_id(user, thread_id))
    except Exception as exc:
        # 删除标记已写，残留的快照不会再被补回库里，只是多占一点 Redis
        logger.warning(f"[History] 会话 {thread_id} 的 Redis 快照没删掉: {exc}")


@app.get("/api/sessions/{thread_id}/messages", response_model=list[Message])
def get_messages(thread_id: str, user: CurrentUserDep) -> list[Message]:
    """登录用户以库为准，库里没有再看 Redis 并顺手补进库；访客只读 Redis。"""
    if not _uses_history_db(user):
        return [_to_message(item) for item in _redis_messages(user, thread_id)]
    try:
        stored = chat_history.load_messages(user.tenant_id, user.id, thread_id)
        if stored is not None:
            return [_to_message(item) for item in stored]
        if chat_history.is_blocked(user.tenant_id, user.id, thread_id):
            raise HTTPException(status_code=404, detail=SESSION_GONE)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[History] 读库失败，改读 Redis: {exc}")
        return [_to_message(item) for item in _redis_messages(user, thread_id)]

    cached = _redis_messages(user, thread_id)
    turns = _successful_turns(cached)
    if turns:
        try:
            chat_history.backfill(user.tenant_id, user.id, thread_id, turns)
        except Exception as exc:
            logger.warning(f"[History] 会话 {thread_id} 补进库失败: {exc}")
    return [_to_message(item) for item in cached]


# ============================================================
# 对话（SSE）
# ============================================================

def _as_llm_request(config: LlmConfig | None) -> LlmRequest | None:
    if config is None or not config.api_key.strip():
        return None
    return LlmRequest(
        api_key=config.api_key.strip(),
        base_url=config.base_url or "https://api.deepseek.com/v1",
        model=config.model.strip() or "deepseek-chat",
        stream=config.stream,
    )


def _chat_events(
    thread_id: str,
    question: str,
    tenant_id: str,
    llm: LlmConfig | None = None,
    user_id: str = "",
    session_id: str = "",
    replace_question: str | None = None,
) -> Iterator[str]:
    """把图的流事件转成 SSE。custom 是生成中的 token，updates 是节点产出。

    user_id 为空（挂件访客）时不读写长期记忆；session_id 为空时这一轮不进会话历史库。
    """
    return stream_with_llm_request(
        _as_llm_request(llm),
        _graph_events(thread_id, question, tenant_id, user_id, session_id, replace_question),
    )


SESSION_GONE = "这个会话不存在或已经删除，请新建对话"


def _rewind_last_turn(graph: Any, config: dict[str, Any], question: str,
                      tenant_id: str, user_id: str, session_id: str) -> None:
    """最后一轮的提问正好是 question 时，把这一轮从 Redis 快照（和库里）删掉，再按新提问重答。"""
    snapshot = graph.get_state(config)
    messages = list(snapshot.values.get("messages") or []) if snapshot and snapshot.values else []
    last_user = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), None)
    if last_user is None or messages[last_user].get("content") != question:
        return
    dropped_answer = messages[last_user + 1] if last_user + 1 < len(messages) else None
    graph.update_state(config, {"messages": messages[:last_user]}, as_node="finalize")
    # 失败的那一轮本来就没进库，库里的最后一轮是更早的，不能删
    if session_id and dropped_answer and not _is_failed_answer(dropped_answer.get("content", "")):
        try:
            chat_history.drop_last_turn(tenant_id, user_id, session_id, question)
        except Exception as exc:
            logger.warning(f"[History] 会话 {session_id} 删除最后一轮失败: {exc}")


def _prepare_history(graph: Any, config: dict[str, Any], input_state: dict[str, Any],
                     tenant_id: str, user_id: str, session_id: str) -> None:
    """把库里的摘要带进这一轮；Redis 快照被清空过的话，用库里最近的消息把对话接上。"""
    try:
        input_state["summary"] = chat_history.summary(tenant_id, user_id, session_id)
        snapshot = graph.get_state(config)
        if snapshot and snapshot.values and snapshot.values.get("messages"):
            return
        restored = chat_history.recent_messages(tenant_id, user_id, session_id, MAX_STORED_TURNS * 2)
        if restored:
            input_state["messages"] = restored
            logger.info(f"[History] 会话 {session_id} 的 Redis 快照没了，从库里接回 {len(restored)} 条")
    except Exception as exc:
        logger.warning(f"[History] 读取会话 {session_id} 的历史失败，本轮只用 Redis: {exc}")


def _persist_turn(graph: Any, config: dict[str, Any], tenant_id: str, user_id: str, session_id: str) -> str | None:
    """这一轮成功就写进库，返回刚写的那条回答的编号（前端拿它来点赞点踩）；没写进去返回 None。"""
    try:
        messages = graph.get_state(config).values.get("messages") or []
        if len(messages) < 2:
            return None
        question, answer = messages[-2], messages[-1]
        if question.get("role") != "user" or answer.get("role") != "assistant":
            return None
        if _is_failed_answer(answer.get("content", "")):
            return None
        seq = chat_history.record_turn(tenant_id, user_id, session_id, question, answer)
        if seq is None:
            return None
    except Exception as exc:
        logger.warning(f"[History] 会话 {session_id} 这一轮没写进库: {exc}")
        return None
    session_summary.schedule(tenant_id, user_id, session_id, chat_endpoint(tenant_id))
    try:
        return chat_history.message_id_at(tenant_id, user_id, session_id, seq)
    except Exception as exc:
        logger.warning(f"[History] 会话 {session_id} 取不到回答编号，这条不能打分: {exc}")
        return None


def _log_question(tenant_id: str, question: str, done: dict[str, Any], tools: list[dict[str, Any]],
                  guest: bool, message_id: str | None) -> None:
    """记一笔匿名的提问结果，给管理员看哪些问题没答上来。失败了只记日志，不能影响回答。"""
    answer = done.get("answer", "")
    if _is_failed_answer(answer) or not usage_log.enabled():
        return
    try:
        names = list(dict.fromkeys(str(t["name"]) for t in tools if t.get("state") == "running" and t.get("name")))
        outcome = usage_stats.classify(done.get("intent", ""), answer, done.get("sources") or [], names, refusal_text(tenant_id))
        usage_log.record_question(tenant_id, question, outcome, names, guest, message_id)
    except Exception as exc:
        logger.warning(f"[Usage] 提问记录没写上: {exc}")


def _graph_events(thread_id: str, question: str, tenant_id: str, user_id: str,
                  session_id: str = "", replace_question: str | None = None) -> Iterator[str]:
    remembered = memory_store.take_remember_command(question) if user_id else None
    if remembered:
        try:
            memory_store.add_memories(tenant_id, user_id, [remembered])
            yield sse("status", {"stage": "remembered", "content": remembered})
        except Exception as exc:
            logger.warning(f"[记忆] 「记住」指令写入失败: {exc}")
    input_state: dict[str, Any] = {
        "user_question": question,
        "route_strategy": ROUTE_STRATEGY,
        "tenant_id": tenant_id,
        "user_id": user_id,
    }
    corpus = knowledge_base.short_corpus(tenant_id)
    if corpus:
        input_state["current_doc_content"] = corpus

    config = {"configurable": {"thread_id": thread_id}}
    persist = bool(user_id and session_id and chat_history.enabled())

    try:
        graph = build_graph()
        if persist:
            _prepare_history(graph, config, input_state, tenant_id, user_id, session_id)
        if replace_question:
            if "messages" in input_state:
                # 快照刚从库里接回来、还没写进 Redis，直接在这份消息上去掉最后一轮
                graph.update_state(config, {"messages": input_state.pop("messages")}, as_node="finalize")
            _rewind_last_turn(graph, config, replace_question, tenant_id, user_id, session_id if persist else "")
        def finish(done: dict[str, Any], tools: list[dict[str, Any]]) -> dict[str, Any]:
            # 先入库再发 done：前端收到 done 就可能关掉连接，之后的代码不一定还会执行
            message_id = _persist_turn(graph, config, tenant_id, user_id, session_id) if persist else None
            # 「重新回答」是同一个问题再答一次，已经算过一回了，不重复记
            if replace_question != question:
                _log_question(tenant_id, question, done, tools, guest=not user_id, message_id=message_id)
            return {"message_id": message_id} if message_id else {}

        yield from sse_from_graph_stream(
            graph.stream(input_state, config=config, stream_mode=["updates", "custom"]), finish
        )
    except Exception as exc:
        yield sse("error", {"message": f"回答生成失败: {exc}"})


def _enforce_guest_quota(user: CurrentUser) -> None:
    embed = embed_settings(user.embed_id)
    if embed is None or not embed.enabled:
        raise HTTPException(status_code=401, detail="该聊天挂件已停用")
    enforce_embed_quota(embed.id, embed.daily_limit)


@app.post("/api/chat")
def chat(payload: ChatRequest, user: CurrentUserDep) -> StreamingResponse:
    enforce_chat_quota(user.id)
    if user.is_guest:
        _enforce_guest_quota(user)
    if _uses_history_db(user):
        try:
            blocked = chat_history.is_blocked(user.tenant_id, user.id, payload.thread_id)
        except Exception as exc:
            logger.warning(f"[History] 会话归属查询失败，照常回答: {exc}")
            blocked = False
        if blocked:
            raise HTTPException(status_code=404, detail=SESSION_GONE)
    try:
        desk_log.remember_question(user.tenant_id, "" if user.is_guest else user.id, payload.question)
    except Exception:
        logger.warning("提问记录没写上")
    return StreamingResponse(
        _chat_events(
            scoped_thread_id(user, payload.thread_id),
            payload.question,
            user.tenant_id,
            # 访客的模型调用一律走学校配置，不接受自带密钥，也没有长期记忆
            None if user.is_guest else payload.llm,
            "" if user.is_guest else user.id,
            session_id=payload.thread_id if _uses_history_db(user) else "",
            replace_question=payload.replace_question,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉 nginx 缓冲，否则 SSE 会被攒到最后一次性吐出
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# 回答评价与使用情况
# ============================================================

@app.post("/api/feedback", status_code=204)
def rate_answer(payload: FeedbackRequest, user: MemberDep) -> None:
    """给自己会话里的一条回答点赞 / 点踩；rating 为 0 是取消。"""
    if not usage_log.enabled():
        raise HTTPException(status_code=503, detail="没有配置数据库，暂时不能评价")
    try:
        found = usage_log.set_feedback(user.tenant_id, user.id, payload.message_id, payload.rating, payload.reason)
    except Exception as exc:
        logger.warning(f"[Usage] 评价没写上: {exc}")
        raise HTTPException(status_code=503, detail="暂时没记上，请稍后再试") from exc
    if not found:
        raise HTTPException(status_code=404, detail="没有这条回答")


@app.get("/api/admin/usage")
def get_usage(user: AdminDep, days: Annotated[int, Query(ge=1, le=90)] = 7) -> dict[str, Any]:
    """管理员看本校最近几天的使用情况：高频问题、没答上来的、点赞点踩、工具调用。"""
    if not usage_log.enabled():
        raise HTTPException(status_code=503, detail="没有配置数据库，看不到使用情况")
    try:
        rows = usage_log.usage_rows(user.tenant_id, days)
    except Exception as exc:
        logger.warning(f"[Usage] 读取使用情况失败: {exc}")
        raise HTTPException(status_code=503, detail="使用情况暂时读不到，请稍后再试") from exc
    return usage_stats.build_report(rows, read_audit(user.tenant_id), tool_labels(), days, time.time())


# ============================================================
# 知识库
# ============================================================

@app.get("/api/documents", response_model=list[DocumentResponse])
def list_documents(user: MemberDep) -> list[DocumentResponse]:
    return [
        DocumentResponse(name=doc.name, chars=doc.chars, indexed=doc.indexed, source=doc.source)
        for doc in knowledge_base.documents(user.tenant_id)
    ]


async def _read_capped(file: UploadFile) -> bytes:
    """分块读取并在超限时立刻中断。

    一次性 await file.read() 会把整个请求体读进内存，
    传一个几 GB 的文件就能把进程撑爆。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/api/documents", response_model=DocumentResponse)
async def upload_document(
    user: KnowledgeManagerDep,
    file: Annotated[UploadFile, File()],
) -> DocumentResponse:
    data = await _read_capped(file)
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    try:
        info = knowledge_base.add_upload(
            file.filename or "未命名文件", data, user.tenant_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DocumentResponse(
        name=info.name, chars=info.chars, indexed=info.indexed, source=info.source
    )


@app.post("/api/documents/url", response_model=DocumentResponse)
def import_url(payload: UrlImport, user: KnowledgeManagerDep) -> DocumentResponse:
    """抓取网页正文导入本校知识库（学校通知、教务公告等）。"""
    try:
        info = knowledge_base.add_url(payload.url, user.tenant_id)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"网页抓取失败: {exc}") from exc
    return DocumentResponse(
        name=info.name, chars=info.chars, indexed=info.indexed, source=info.source
    )


@app.delete("/api/documents")
def clear_documents(user: KnowledgeManagerDep) -> dict[str, str]:
    """只清本校上传的资料；维基与预置文档属于全局，不受影响。"""
    knowledge_base.clear(user.tenant_id)
    return {"status": "cleared"}


# ============================================================
# 长期记忆
# ============================================================

def _memory_response(note: memory_store.MemoryNote) -> MemoryResponse:
    return MemoryResponse(
        id=note.id, content=note.content, created_at=note.created_at, origin=note.origin
    )


@app.get("/api/memories", response_model=list[MemoryResponse])
def list_memories(user: MemberDep) -> list[MemoryResponse]:
    notes = memory_store.list_memories(user.tenant_id, user.id)
    return [_memory_response(note) for note in notes]


@app.post("/api/memories", response_model=list[MemoryResponse])
def add_memories(payload: MemoryCreate, user: MemberDep) -> list[MemoryResponse]:
    """一次可写多条：浏览器里旧版 localStorage 记忆迁移时整批上传。"""
    added = memory_store.add_memories(user.tenant_id, user.id, payload.contents)
    return [_memory_response(note) for note in added]


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str, user: MemberDep) -> dict[str, str]:
    if not memory_store.delete_memory(user.tenant_id, user.id, memory_id):
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"status": "deleted"}
