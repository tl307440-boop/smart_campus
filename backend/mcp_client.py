"""标准 MCP (Model Context Protocol) Host / Client 管理器。

职责：
1. 在独立后台事件循环线程里以标准 stdio JSON-RPC 连接本地减压 MCP Server (`mcp_wellness.py`)
   以及环境变量 `MCP_SERVERS_JSON` 中额外配置的标准外部 MCP Server；
2. 遵循官方 MCP 协议执行 `initialize()` -> `list_tools()` 工具发现 -> `call_tool()` 调用；
3. 安全边界：暴露给大模型的工具参数 schema 自动剔除 `tenant_id` / `user_id`，
   调用时由服务端宿主层从认证上下文强制注入，防止模型幻觉或越权串号。
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from logging_config import get_logger
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = get_logger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent
_WELLNESS_SCRIPT = _BACKEND_DIR / "mcp_wellness.py"
_DEFAULT_TIMEOUT = 12.0


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    command: str
    args: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None


@dataclass(frozen=True)
class DiscoveredMcpTool:
    server: str
    name: str
    title: str
    description: str
    llm_schema: dict[str, Any]
    inject_tenant: bool
    inject_user: bool


def _builtin_specs() -> list[McpServerSpec]:
    specs: list[McpServerSpec] = []
    if _WELLNESS_SCRIPT.exists():
        env = dict(os.environ)
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{_BACKEND_DIR}{os.pathsep}{existing_pp}" if existing_pp else str(_BACKEND_DIR)
        )
        # 确保本地 SQLite 回退路径在主进程与 stdio 子进程间完全一致
        if not env.get("DATABASE_URL") and not env.get("WELLNESS_SQLITE_PATH"):
            import wellness_store

            env["WELLNESS_SQLITE_PATH"] = wellness_store._sqlite_path()
        specs.append(
            McpServerSpec(
                name="zhiyue-wellness",
                command=sys.executable,
                args=(str(_WELLNESS_SCRIPT),),
                env=env,
                cwd=str(_BACKEND_DIR),
            )
        )
    extra_raw = os.getenv("MCP_SERVERS_JSON", "").strip()
    if extra_raw:
        try:
            parsed = json.loads(extra_raw)
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict) or not item.get("command") or not item.get("name"):
                        continue
                    specs.append(
                        McpServerSpec(
                            name=str(item["name"]),
                            command=str(item["command"]),
                            args=tuple(str(a) for a in (item.get("args") or [])),
                            env={**os.environ, **{str(k): str(v) for k, v in (item.get("env") or {}).items()}},
                            cwd=str(item["cwd"]) if item.get("cwd") else None,
                        )
                    )
        except Exception as exc:
            logger.warning(f"[MCP] 解析 MCP_SERVERS_JSON 失败: {exc}")
    return specs


def _strip_host_params(schema: dict[str, Any] | None) -> tuple[dict[str, Any], bool, bool]:
    """从工具 schema 里剥离 tenant_id / user_id，防止 LLM 自行编造身份参数。"""
    clean = copy.deepcopy(schema) if isinstance(schema, dict) else {"type": "object", "properties": {}}
    props = clean.get("properties")
    if not isinstance(props, dict):
        props = {}
        clean["properties"] = props
    inject_tenant = "tenant_id" in props
    inject_user = "user_id" in props
    props.pop("tenant_id", None)
    props.pop("user_id", None)
    req = clean.get("required")
    if isinstance(req, list):
        clean["required"] = [r for r in req if r not in {"tenant_id", "user_id"}]
    return clean, inject_tenant, inject_user


def _extract_result_payload(result: Any) -> dict[str, Any]:
    """兼容 FastMCP 的 structuredContent 与标准 TextContent JSON 载荷。"""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    texts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    joined = "\n".join(texts)
    if joined:
        try:
            parsed = json.loads(joined)
            if isinstance(parsed, dict):
                return parsed
            return {"result": parsed}
        except ValueError:
            return {"text": joined}
    return {"ok": not bool(getattr(result, "isError", False))}


class McpHostManager:
    """持久化管理后台 stdio MCP 会话，为同步 Agent 提供线程安全调用。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stack: AsyncExitStack | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, DiscoveredMcpTool] = {}
        self._server_errors: dict[str, str] = {}
        self._initialized = False

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None and self._loop.is_running():
            return self._loop
        ready = threading.Event()
        holder: list[asyncio.AbstractEventLoop] = []

        def runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            holder.append(loop)
            ready.set()
            loop.run_forever()

        t = threading.Thread(target=runner, name="mcp-host-loop", daemon=True)
        t.start()
        ready.wait(timeout=5.0)
        self._thread = t
        self._loop = holder[0]
        return self._loop

    async def _connect_all(self) -> None:
        await self._close_sessions()
        stack = AsyncExitStack()
        await stack.__aenter__()
        self._stack = stack
        self._sessions = {}
        self._tools = {}
        self._server_errors = {}

        for spec in _builtin_specs():
            try:
                params = StdioServerParameters(
                    command=spec.command,
                    args=list(spec.args),
                    env=spec.env,
                    cwd=spec.cwd,
                )
                read_stream, write_stream = await asyncio.wait_for(
                    stack.enter_async_context(stdio_client(params)),
                    timeout=_DEFAULT_TIMEOUT,
                )
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await asyncio.wait_for(session.initialize(), timeout=_DEFAULT_TIMEOUT)
                listed = await asyncio.wait_for(session.list_tools(), timeout=_DEFAULT_TIMEOUT)
                self._sessions[spec.name] = session
                for tool in listed.tools:
                    schema, inj_t, inj_u = _strip_host_params(tool.inputSchema)
                    self._tools[tool.name] = DiscoveredMcpTool(
                        server=spec.name,
                        name=tool.name,
                        title=str(getattr(tool, "title", None) or tool.name),
                        description=str(tool.description or ""),
                        llm_schema=schema,
                        inject_tenant=inj_t,
                        inject_user=inj_u,
                    )
                logger.info(f"[MCP] 已挂载服务器 {spec.name}，发现 {len(listed.tools)} 个工具")
            except Exception as exc:
                self._server_errors[spec.name] = str(exc)
                logger.warning(f"[MCP] 挂载服务器 {spec.name} 失败: {exc}")
        self._initialized = True

    async def _close_sessions(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                pass
            self._stack = None
        self._sessions = {}
        self._tools = {}

    def _run_coro(self, coro: Any, timeout: float = 20.0) -> Any:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)

    def ensure_started(self, *, force_reload: bool = False) -> None:
        if os.getenv("MCP_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            return
        with self._lock:
            if self._initialized and not force_reload and self._sessions:
                return
            self._run_coro(self._connect_all(), timeout=25.0)

    def list_tools(self) -> list[DiscoveredMcpTool]:
        try:
            self.ensure_started()
        except Exception as exc:
            logger.warning(f"[MCP] 初始化工具列表失败: {exc}")
            return []
        return list(self._tools.values())

    def call_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        self.ensure_started()
        tool = self._tools.get(name)
        if tool is None:
            raise RuntimeError(f"未发现 MCP 工具: {name}")
        session = self._sessions.get(tool.server)
        if session is None:
            raise RuntimeError(f"MCP 服务 {tool.server} 未连接")

        payload = dict(args or {})
        if tool.inject_tenant:
            payload["tenant_id"] = tenant_id
        if tool.inject_user:
            payload["user_id"] = user_id

        try:
            res = self._run_coro(session.call_tool(name, payload), timeout=20.0)
        except Exception as exc:
            # stdio 进程异常退出时重连一次再试
            logger.warning(f"[MCP] 工具 {name} 调用异常 ({exc})，尝试重连 MCP Server...")
            self.ensure_started(force_reload=True)
            session = self._sessions.get(tool.server)
            if session is None:
                raise
            res = self._run_coro(session.call_tool(name, payload), timeout=20.0)

        data = _extract_result_payload(res)
        if getattr(res, "isError", False) and not data.get("error"):
            data["error"] = data.get("text") or "MCP 工具执行失败"
        return data

    def status(self) -> dict[str, Any]:
        tools = self.list_tools()
        return {
            "enabled": os.getenv("MCP_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"},
            "servers": [
                {
                    "name": name,
                    "connected": True,
                    "transport": "stdio",
                    "tool_count": sum(1 for t in tools if t.server == name),
                }
                for name in self._sessions
            ]
            + [
                {"name": name, "connected": False, "transport": "stdio", "error": err}
                for name, err in self._server_errors.items()
            ],
            "tools": [
                {
                    "server": t.server,
                    "name": t.name,
                    "title": t.title,
                    "description": t.description,
                }
                for t in tools
            ],
        }

    def close(self) -> None:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                try:
                    self._run_coro(self._close_sessions(), timeout=5.0)
                except Exception:
                    pass
                self._loop.call_soon_threadsafe(self._loop.stop)
            self._initialized = False


manager = McpHostManager()
