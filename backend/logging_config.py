"""统一日志配置。

原来全项目用 print：没有级别、没有时间戳、无法在生产里按严重程度过滤，
容器日志收集端也拿不到结构化字段。

调用 setup_logging() 一次即可（api.py 在 lifespan 里调），
其余模块只需 logging.getLogger(__name__)。
"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False

_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def log_level() -> int:
    name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if name not in _LEVELS:
        name = "INFO"
    return getattr(logging, name)


def setup_logging() -> None:
    """重复调用安全。日志走 stdout，交给容器运行时收集。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.setLevel(log_level())
    # 直接 addHandler 会在 uvicorn --reload 重载时叠加出重复输出
    root.handlers = [handler]

    # 这两个库在 INFO 级别很吵，日常排障用不到
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
