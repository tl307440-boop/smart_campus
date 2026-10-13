"""单次对话里，浏览器带来的模型配置。密钥只活在这一次请求，不写盘。"""

from __future__ import annotations

from collections.abc import Iterator
from contextvars import ContextVar, Token, copy_context
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class LlmRequest:
    api_key: str
    base_url: str
    model: str
    stream: bool = True


_current: ContextVar[LlmRequest | None] = ContextVar("llm_request", default=None)


def current_llm_request() -> LlmRequest | None:
    return _current.get()


def push_llm_request(request: LlmRequest | None) -> Token[LlmRequest | None]:
    return _current.set(request)


def pop_llm_request(token: Token[LlmRequest | None]) -> None:
    _current.reset(token)


def stream_with_llm_request(request: LlmRequest | None, events: Iterator[T]) -> Iterator[T]:
    """在同一份上下文里逐个取 events。

    StreamingResponse 每次 next() 都在一份新的上下文副本里跑，生成器里 set 的值到下一次 next() 就没了，
    最后 reset 还会因为「不是同一个上下文」抛错，把流掐断。
    """
    context = copy_context()
    context.run(_current.set, request)
    try:
        while True:
            try:
                yield context.run(next, events)
            except StopIteration:
                return
    finally:
        close = getattr(events, "close", None)
        if close is not None:
            context.run(close)
