"""把 LangGraph 的流事件翻译成 SSE 帧。

单独成模块是为了能脱离图、Redis 和大模型直接测这段线上协议，
见 test_chat_stream.py。api.py 只负责把图接上来。
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from typing import Any

# 结束时的回调：拿到最终答案和这一轮用过的工具事件，返回要并进 done 帧的额外字段
Finish = Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any] | None]


def sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sse_from_graph_stream(events: Iterable[tuple[str, Any]], finish: Finish | None = None) -> Iterator[str]:
    """消费 graph.stream(stream_mode=["updates", "custom"]) 的 (mode, chunk) 序列。

    custom 是生成中的 token，来一个发一个；updates 是节点产出，用来发进度，
    并在结束时给出权威的完整答案（与写进 Redis 历史的那份一致）。

    finish 在发 done 之前调用：入库、记录提问都放在这里，保证前端收到 done 时已经写完。
    它返回的字段（比如刚入库那条回答的编号）会并进 done。它出错不能拖垮回答，由调用方自己兜住。
    """
    intent = ""
    context = ""
    sources: list[dict[str, Any]] = []
    answer = ""
    emotion: dict[str, Any] | None = None
    safety: dict[str, Any] | None = None
    tables: list[dict[str, Any]] = []
    follow_ups: list[str] = []
    tool_events: list[dict[str, Any]] = []
    generating_announced = False

    for mode, chunk in events:
        if mode == "custom":
            tool = chunk.get("tool") if isinstance(chunk, dict) else None
            if isinstance(tool, dict):
                tool_events.append(tool)
                yield sse("tool", tool)
                continue
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
                sources = patch.get("sources") or []
                yield sse(
                    "status",
                    {"stage": "retrieved", "chars": len(context), "sources": len(sources)},
                )
            elif node == "generate":
                answer = patch.get("final_answer", "") or ""
                # 工具问答的来源（教务、通知、地图链接）和表格在生成时才有
                if patch.get("sources"):
                    sources = patch["sources"]
                tables = patch.get("tables") or []
                follow_ups = patch.get("follow_ups") or []
            elif node == "emotion":
                emotion = patch.get("emotion_data")
            # 求助卡片：高危关键词在 generate 给出，情绪分析判出高风险时在 finalize 补上
            if node in ("generate", "finalize") and patch.get("safety"):
                safety = patch["safety"]

    done: dict[str, Any] = {
        "answer": answer,
        "intent": intent,
        "context": context,
        "sources": sources,
        "emotion": emotion,
        "safety": safety,
        "tables": tables,
        "follow_ups": follow_ups,
    }
    if finish is not None:
        done.update(finish(done, tool_events) or {})
    yield sse("done", done)
