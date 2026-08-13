"""FastAPI 层：把 LangGraph 对话与知识库能力暴露为 HTTP/SSE 接口，供独立前端调用。

启动方式:
    uvicorn api:app --reload --port 8001     # 在 backend/ 目录下执行；8001 避开本机已占用的 8000
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterator, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from env_config import load_backend_env
from graph_app import build_graph
from knowledge import knowledge_base

load_backend_env()

# 检索子路由交给 graph_app 按文档长度自动判定
ROUTE_STRATEGY = "auto"

# 服务端把整段回答切片下发，前端据此做打字机效果
STREAM_CHUNK_CHARS = 4
STREAM_CHUNK_DELAY = 0.012

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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
    allow_methods=["*"],
    allow_headers=["*"],
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
    emotion: Optional[dict[str, Any]] = None


class SessionResponse(BaseModel):
    thread_id: str


class DocumentResponse(BaseModel):
    name: str
    chars: int
    indexed: bool
    source: str


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ============================================================
# 会话
# ============================================================

@app.post("/api/sessions", response_model=SessionResponse)
def create_session() -> SessionResponse:
    return SessionResponse(thread_id=str(uuid.uuid4()))


@app.get("/api/sessions/{thread_id}/messages", response_model=list[Message])
def get_messages(thread_id: str) -> list[Message]:
    """从 Redis 快照恢复该会话的历史消息。"""
    try:
        graph = build_graph()
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
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

def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chat_events(thread_id: str, question: str) -> Iterator[str]:
    """按图节点推进逐段下发进度，生成节点完成后再切片吐出回答。"""
    input_state: dict[str, Any] = {
        "user_question": question,
        "route_strategy": ROUTE_STRATEGY,
    }
    corpus = knowledge_base.short_corpus()
    if corpus:
        input_state["current_doc_content"] = corpus

    config = {"configurable": {"thread_id": thread_id}}

    intent = ""
    context = ""
    answer = ""
    emotion: Optional[dict[str, Any]] = None

    try:
        graph = build_graph()
        for update in graph.stream(input_state, config=config, stream_mode="updates"):
            for node, patch in update.items():
                if not isinstance(patch, dict):
                    continue

                if node == "intent":
                    intent = patch.get("intent", "")
                    yield _sse("status", {"stage": "intent", "intent": intent})
                elif node == "retrieve":
                    context = patch.get("context", "") or ""
                    yield _sse("status", {"stage": "retrieved", "chars": len(context)})
                elif node == "generate":
                    answer = patch.get("final_answer", "") or ""
                    yield _sse("status", {"stage": "generating"})
                    for i in range(0, len(answer), STREAM_CHUNK_CHARS):
                        yield _sse("delta", {"text": answer[i:i + STREAM_CHUNK_CHARS]})
                        time.sleep(STREAM_CHUNK_DELAY)
                elif node == "emotion":
                    emotion = patch.get("emotion_data")
    except Exception as exc:
        yield _sse("error", {"message": f"回答生成失败: {exc}"})
        return

    yield _sse(
        "done",
        {
            "answer": answer,
            "intent": intent,
            "context": context,
            "emotion": emotion,
        },
    )


@app.post("/api/chat")
def chat(payload: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _chat_events(payload.thread_id, payload.question),
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
def list_documents() -> list[DocumentResponse]:
    return [DocumentResponse(**doc.__dict__) for doc in knowledge_base.documents()]


@app.post("/api/documents", response_model=DocumentResponse)
async def upload_document(file: UploadFile = File(...)) -> DocumentResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    try:
        info = knowledge_base.add_upload(file.filename or "未命名文件", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DocumentResponse(**info.__dict__)


@app.delete("/api/documents")
def clear_documents() -> dict[str, str]:
    knowledge_base.clear()
    return {"status": "cleared"}
