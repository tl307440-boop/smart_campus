"""把 LangGraph 的流事件翻译成 SSE 帧。

单独成模块是为了能脱离图、Redis 和大模型直接测这段线上协议，
见 test_chat_stream.py。api.py 只负责把图接上来。
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any


def sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sse_from_graph_stream(events: Iterable[tuple[str, Any]]) -> Iterator[str]:
    """消费 graph.stream(stream_mode=["updates", "custom"]) 的 (mode, chunk) 序列。

    custom 是生成中的 token，来一个发一个；updates 是节点产出，用来发进度，
    并在结束时给出权威的完整答案（与写进 Redis 历史的那份一致）。
    """
    intent = ""
    context = ""
    answer = ""
    emotion: dict[str, Any] | None = None
    generating_announced = False

    for mode, chunk in events:
        if mode == "custom":
            delta = chunk.get("delta") if isinstance(chunk, dict) else None
            if not delta:
                continue
            if not generating_announced:
                generating_announced = True
                yield sse("status", {"stage": "generating"})
            yield sse("delta", {"text": delta})
            continue

        if mode != "updates" or not isinstance(chunk, dict):
            continue

        for node, patch in chunk.items():
            if not isinstance(patch, dict):
                continue

            if node == "intent":
                intent = patch.get("intent", "")
                yield sse("status", {"stage": "intent", "intent": intent})
            elif node == "retrieve":
                context = patch.get("context", "") or ""
                yield sse("status", {"stage": "retrieved", "chars": len(context)})
            elif node == "generate":
                answer = patch.get("final_answer", "") or ""
            elif node == "emotion":
                emotion = patch.get("emotion_data")

    yield sse(
        "done",
        {
            "answer": answer,
            "intent": intent,
            "context": context,
            "emotion": emotion,
        },
    )
