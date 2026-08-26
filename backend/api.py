"""FastAPI 层：把 LangGraph 对话与知识库能力暴露为 HTTP/SSE 接口，供独立前端调用。

启动方式:
    uvicorn api:app --reload --port 8001     # 在 backend/ 目录下执行；8001 避开本机已占用的 8000
"""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from auth import CurrentUserDep, KnowledgeManagerDep, jwt_secret, scoped_thread_id
from chat_stream import sse, sse_from_graph_stream
from env_config import load_backend_env
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from graph_app import build_graph
from knowledge import knowledge_base
from logging_config import setup_logging
from pydantic import BaseModel, Field
from rate_limit import enforce_chat_quota

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

class ChatRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)


class Message(BaseModel):
    role: str
    content: str
    context: str = ""
    emotion: dict[str, Any] | None = None


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


# ============================================================
# 会话
# ============================================================

@app.post("/api/sessions", response_model=SessionResponse)
def create_session(_: CurrentUserDep) -> SessionResponse:
    return SessionResponse(thread_id=str(uuid.uuid4()))


@app.get("/api/sessions/{thread_id}/messages", response_model=list[Message])
def get_messages(thread_id: str, user: CurrentUserDep) -> list[Message]:
    """从 Redis 快照恢复该会话的历史消息。"""
    try:
        graph = build_graph()
        snapshot = graph.get_state(
            {"configurable": {"thread_id": scoped_thread_id(user, thread_id)}}
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"无法读取会话历史（检查 Redis）: {exc}") from exc

    stored = snapshot.values.get("messages") if snapshot and snapshot.values else None
    if not stored:
        return []

    return [
        Message(
            role=item.get("role", ""),
            content=item.get("content", ""),
            context=item.get("context", "") or "",
            emotion=item.get("emotion"),
        )
        for item in stored
        if item.get("role") in ("user", "assistant") and item.get("content")
    ]


# ============================================================
# 对话（SSE）
# ============================================================

def _chat_events(thread_id: str, question: str, tenant_id: str) -> Iterator[str]:
    """把图的流事件转成 SSE。custom 是生成中的 token，updates 是节点产出。"""
    input_state: dict[str, Any] = {
        "user_question": question,
        "route_strategy": ROUTE_STRATEGY,
        "tenant_id": tenant_id,
    }
    corpus = knowledge_base.short_corpus(tenant_id)
    if corpus:
        input_state["current_doc_content"] = corpus

    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph = build_graph()
        yield from sse_from_graph_stream(
            graph.stream(input_state, config=config, stream_mode=["updates", "custom"])
        )
    except Exception as exc:
        yield sse("error", {"message": f"回答生成失败: {exc}"})


@app.post("/api/chat")
def chat(payload: ChatRequest, user: CurrentUserDep) -> StreamingResponse:
    enforce_chat_quota(user.id)
    return StreamingResponse(
        _chat_events(
            scoped_thread_id(user, payload.thread_id),
            payload.question,
            user.tenant_id,
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
# 知识库
# ============================================================

@app.get("/api/documents", response_model=list[DocumentResponse])
def list_documents(user: CurrentUserDep) -> list[DocumentResponse]:
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


@app.delete("/api/documents")
def clear_documents(user: KnowledgeManagerDep) -> dict[str, str]:
    """只清本校上传的资料；维基与预置文档属于全局，不受影响。"""
    knowledge_base.clear(user.tenant_id)
    return {"status": "cleared"}
