"""单次对话里，浏览器带来的模型配置。密钥只活在这一次请求，不写盘。"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


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
