"""对话模型与长文档精读该用哪把密钥。

对话模型（意图判定、闲聊、按资料回答、情绪分析共用）：
    本人在浏览器里填的密钥（只活在这一次请求）> 学校在「AI 服务」页配的 > 服务器 .env
长文档精读：学校配的 > 服务器 .env（个人密钥不参与）。
嵌入挂件的访客不接受自带密钥（api.chat 里丢掉了），自然落到学校或平台。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from env_config import load_backend_env
from llm_request import current_llm_request
from openai import OpenAI
from workspace import school_ai, school_vision

load_backend_env()

DEFAULT_CHAT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_CHAT_MODEL = "deepseek-chat"
DEFAULT_READER_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_READER_MODEL = "kimi-k2-0905-preview"
EMBEDDING_MODEL = "embedding-3"
# 只预留名字，不加载、不打进镜像。2 核 2G 的机器装上 PyTorch 容易把内存吃满。
RESERVED_LOCAL_EMBEDDING_MODELS = (
    "BAAI/bge-small-zh-v1.5",
    "BAAI/bge-base-zh-v1.5",
    "BAAI/bge-m3",
)
EmbeddingProvider = Literal["local", "zhipu", "openai"]

Source = Literal["personal", "school", "platform"]
SOURCE_LABEL: dict[str, str] = {"personal": "个人密钥", "school": "学校配置", "platform": "平台默认"}


def _env_float(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


# 不设超时的话，上游卡住会一直占着连接和线程池名额
LLM_TIMEOUT_SECONDS = _env_float("LLM_TIMEOUT_SECONDS", 60.0)
LLM_MAX_RETRIES = int(_env_float("LLM_MAX_RETRIES", 2))
# 精读长文档比普通对话慢，超时给得宽一些
READER_TIMEOUT_SECONDS = _env_float("KIMI_TIMEOUT_SECONDS", 120.0)


@dataclass(frozen=True)
class ModelEndpoint:
    api_key: str
    base_url: str
    model: str
    source: Source
    stream: bool = True

    @property
    def label(self) -> str:
        return SOURCE_LABEL[self.source]


def _env_key(name: str) -> str:
    value = os.getenv(name, "").strip()
    # 照抄 .env.example 留下的占位符等于没填
    return "" if value.startswith("your_") else value


def _base_url(value: str | None, default: str) -> str:
    cleaned = (value or "").strip().rstrip("/")
    # 旧版 .env.example 把 kimi_url 写成了完整的 /chat/completions，SDK 会再拼一次
    if cleaned.endswith("/chat/completions"):
        cleaned = cleaned[: -len("/chat/completions")]
    return cleaned or default


def platform_chat_endpoint() -> ModelEndpoint | None:
    key = _env_key("deepseek_API_key")
    if not key:
        return None
    return ModelEndpoint(
        key,
        _base_url(os.getenv("deepseek_url"), DEFAULT_CHAT_BASE_URL),
        os.getenv("deepseek_model", "").strip() or DEFAULT_CHAT_MODEL,
        "platform",
    )


def platform_reader_endpoint() -> ModelEndpoint | None:
    key = _env_key("kimi_API_key")
    if not key:
        return None
    return ModelEndpoint(
        key,
        _base_url(os.getenv("kimi_url"), DEFAULT_READER_BASE_URL),
        os.getenv("kimi_model", "").strip() or DEFAULT_READER_MODEL,
        "platform",
    )


def school_chat_endpoint(tenant_id: str) -> ModelEndpoint | None:
    """不看个人密钥的对话模型：状态页和连通测试用它，问答链路用 chat_endpoint。"""
    school = school_ai(tenant_id)
    if school.chat_key:
        return ModelEndpoint(
            school.chat_key,
            _base_url(school.chat_base_url, DEFAULT_CHAT_BASE_URL),
            school.chat_model or DEFAULT_CHAT_MODEL,
            "school",
        )
    return platform_chat_endpoint()


def chat_endpoint(tenant_id: str) -> ModelEndpoint | None:
    """这一次问答该用的对话模型；三处都没配时返回 None，调用方据此报「未配置」。"""
    personal = current_llm_request()
    if personal and personal.api_key:
        return ModelEndpoint(
            personal.api_key,
            _base_url(personal.base_url, DEFAULT_CHAT_BASE_URL),
            personal.model or DEFAULT_CHAT_MODEL,
            "personal",
            personal.stream,
        )
    return school_chat_endpoint(tenant_id)


def reader_endpoint(tenant_id: str) -> ModelEndpoint | None:
    school = school_ai(tenant_id)
    if school.reader_key:
        return ModelEndpoint(
            school.reader_key,
            _base_url(school.reader_base_url, DEFAULT_READER_BASE_URL),
            school.reader_model or DEFAULT_READER_MODEL,
            "school",
        )
    return platform_reader_endpoint()


def vision_endpoint(tenant_id: str) -> ModelEndpoint | None:
    """学校配的识图模型。没有平台默认也没有个人密钥：识图会把学生的图片发给第三方，只能是学校明确选定的那一家。

    网址、模型、密钥三项都填了才算开通。
    """
    vision = school_vision(tenant_id)
    if not (vision.base_url and vision.model and vision.key):
        return None
    return ModelEndpoint(vision.key, _base_url(vision.base_url, vision.base_url), vision.model, "school")


@dataclass(frozen=True)
class ToolKey:
    """高德、博查这类只有一把 Key 的服务。"""

    key: str
    source: Source

    @property
    def label(self) -> str:
        return SOURCE_LABEL[self.source]


def _tool_key(school_value: str, env_name: str) -> ToolKey | None:
    if school_value:
        return ToolKey(school_value, "school")
    platform = _env_key(env_name)
    return ToolKey(platform, "platform") if platform else None


def map_key(tenant_id: str) -> ToolKey | None:
    """高德 Web 服务 Key：学校配的 > 服务器 .env 的 AMAP_WEB_KEY。"""
    return _tool_key(school_ai(tenant_id).map_key, "AMAP_WEB_KEY")


def search_key(tenant_id: str) -> ToolKey | None:
    """博查 Web Search API Key：学校配的 > 服务器 .env 的 BOCHA_API_KEY。"""
    return _tool_key(school_ai(tenant_id).search_key, "BOCHA_API_KEY")


@dataclass(frozen=True)
class EmbeddingSpec:
    """智谱 embedding-3。计算在智谱那边，这台机器只存切片后的向量。"""

    provider: EmbeddingProvider
    model: str
    base_url: str
    api_key: str
    source: Source

    @property
    def collection_name(self) -> str:
        return "campus_knowledge"


def embedding_spec(tenant_id: str) -> EmbeddingSpec | None:
    """学校管理员在「AI 服务」里填的智谱密钥 > 服务器 .env。

    local / openai 只是预留（名单见 RESERVED_LOCAL_EMBEDDING_MODELS），看到也当成没选，
    避免在服务器上加载开源模型。
    """
    school = school_ai(tenant_id)
    key = school.embedding_key or _env_key("zhi_pu_API_KEY")
    if not key:
        return None
    source: Source = "school" if school.embedding_key else "platform"
    return EmbeddingSpec("zhipu", EMBEDDING_MODEL, "", key, source)


def embedding_configured(tenant_id: str) -> bool:
    """这所学校现在能不能做向量检索。"""
    return embedding_spec(tenant_id) is not None


@lru_cache(maxsize=32)
def _client(api_key: str, base_url: str, timeout: float, retries: int) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=retries)


def client_for(
    endpoint: ModelEndpoint,
    timeout: float = LLM_TIMEOUT_SECONDS,
    retries: int = LLM_MAX_RETRIES,
) -> OpenAI:
    """同一把密钥复用一个客户端（连接池），不必每轮新建。"""
    return _client(endpoint.api_key, endpoint.base_url, timeout, retries)


def scrub(detail: str, endpoint: ModelEndpoint | ToolKey | None) -> str:
    """上游报错有时会回显密钥，写进回答或日志前抹掉。"""
    secret = getattr(endpoint, "api_key", "") or getattr(endpoint, "key", "")
    if secret and secret in detail:
        return detail.replace(secret, "***")
    return detail
