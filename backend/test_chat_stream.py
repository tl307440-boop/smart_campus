"""SSE 协议回归测试。

用合成的 (mode, chunk) 序列驱动，不需要图、Redis 或大模型:
    cd backend && pytest test_chat_stream.py
"""
from __future__ import annotations

import json

from chat_stream import sse_from_graph_stream


def parse(frames: list[str]) -> list[tuple[str, dict]]:
    """把 SSE 文本帧还原成 (event, payload)，顺便校验帧格式。"""
    parsed: list[tuple[str, dict]] = []
    for frame in frames:
        assert frame.endswith("\n\n"), "每帧必须以空行结尾，否则浏览器不会分帧"
        lines = frame.rstrip("\n").split("\n")
        assert lines[0].startswith("event: ")
        assert lines[1].startswith("data: ")
        parsed.append((lines[0][len("event: "):], json.loads(lines[1][len("data: "):])))
    return parsed


def test_tokens_are_forwarded_one_by_one() -> None:
    """模型吐几块就发几帧，不再等整段生成完再切片。"""
    events = [
        ("updates", {"intent": {"intent": "RAG"}}),
        ("updates", {"retrieve": {"context": "校规第三条"}}),
        ("custom", {"delta": "同学"}),
        ("custom", {"delta": "你好"}),
        ("updates", {"generate": {"final_answer": "同学你好"}}),
        ("updates", {"emotion": {"emotion_data": {"core_emotion": "平静"}}}),
    ]

    frames = parse(list(sse_from_graph_stream(events)))
    kinds = [name for name, _ in frames]

    assert kinds == ["status", "status", "status", "delta", "delta", "done"]
    assert [p["text"] for name, p in frames if name == "delta"] == ["同学", "你好"]


def test_generating_status_precedes_first_token() -> None:
    """「正在组织回答」必须在第一个字之前，而不是生成完之后。"""
    events = [("custom", {"delta": "甲"}), ("custom", {"delta": "乙"})]
    frames = parse(list(sse_from_graph_stream(events)))

    assert frames[0] == ("status", {"stage": "generating"})
    # 只宣告一次，不是每个 token 都发一遍
    assert sum(1 for name, p in frames if name == "status") == 1


def test_done_carries_authoritative_answer() -> None:
    """done 用节点产出，而不是 delta 拼接结果——它才是写进 Redis 的那份。"""
    events = [
        ("custom", {"delta": "部分"}),
        ("updates", {"generate": {"final_answer": "部分内容被节点补全"}}),
    ]
    _, payload = parse(list(sse_from_graph_stream(events)))[-1]

    assert payload["answer"] == "部分内容被节点补全"


def test_done_is_always_last_and_complete() -> None:
    events = [
        ("updates", {"intent": {"intent": "DIRECT"}}),
        ("custom", {"delta": "嗨"}),
        ("updates", {"generate": {"final_answer": "嗨"}}),
        ("updates", {"emotion": {"emotion_data": {"risk_level": 1}}}),
    ]
    frames = parse(list(sse_from_graph_stream(events)))
    name, payload = frames[-1]

    assert name == "done"
    assert payload == {
        "answer": "嗨",
        "intent": "DIRECT",
        "context": "",
        "sources": [],
        "emotion": {"risk_level": 1},
        "safety": None,
    }


def test_safety_card_from_generate_or_finalize_reaches_done() -> None:
    card = {"level": "crisis", "hotlines": [], "school": ""}
    for node in ("generate", "finalize"):
        events = [
            ("updates", {"generate": {"final_answer": "我在", "safety": card if node == "generate" else None}}),
            ("updates", {"finalize": {"safety": card} if node == "finalize" else {}}),
        ]
        _, payload = parse(list(sse_from_graph_stream(events)))[-1]
        assert payload["safety"] == card


def test_empty_and_malformed_chunks_are_skipped() -> None:
    """空 delta、非字典 chunk 不应该产生垃圾帧或抛异常。"""
    events = [
        ("custom", {"delta": ""}),
        ("custom", None),
        ("custom", "不是字典"),
        ("updates", {"generate": None}),
        ("updates", "不是字典"),
        ("values", {"whatever": 1}),
    ]
    frames = parse(list(sse_from_graph_stream(events)))

    assert [name for name, _ in frames] == ["done"]


def test_context_length_is_reported() -> None:
    events = [("updates", {"retrieve": {"context": "12345"}})]
    frames = parse(list(sse_from_graph_stream(events)))

    assert frames[0] == ("status", {"stage": "retrieved", "chars": 5, "sources": 0})


def test_sources_reach_done() -> None:
    """引用卡片的数据跟着 done 一起下发，与写进历史的那份一致。"""
    source = {"index": 1, "source": "学生手册.pdf", "snippet": "第三条", "score": 0.8}
    events = [("updates", {"retrieve": {"context": "第三条", "sources": [source]}})]
    frames = parse(list(sse_from_graph_stream(events)))

    assert frames[0][1]["sources"] == 1
    assert frames[-1][1]["sources"] == [source]


def test_chinese_is_not_escaped() -> None:
    """ensure_ascii=False，否则前端拿到的是 \\uXXXX。"""
    frames = list(sse_from_graph_stream([("custom", {"delta": "校规"})]))

    assert "校规" in frames[1]
