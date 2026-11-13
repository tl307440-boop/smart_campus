"""企业微信智能机器人长连接客户端（WeCom Smart Bot WebSocket Gateway）。

特点：
1. 无需公网回调域名、无需配置回调 Token / EncodingAESKey 解密；
2. 由后端主动建连到 `wss://openws.work.weixin.qq.com`，使用学校自配的 `bot_id` + `secret` 鉴权；
3. 收到消息后通过 `im_gateway.handle_incoming_message` 接入校园智能体并以 `aibot_respond_msg` 回复；
4. 保留已建连会话，支持早报/出分/工单回复经由 `aibot_send_msg` 主动下发给学生。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import uuid
from typing import Any

import im_gateway
from logging_config import get_logger
from tenancy import GLOBAL_TENANT
from workspace import SchoolIm, list_configured_im_tenants, school_im

logger = get_logger(__name__)

DEFAULT_WECOM_WS_URL = "wss://openws.work.weixin.qq.com"
HEARTBEAT_SECONDS = 25.0
_MENTION_PREFIX_RE = re.compile(r"^(?:@[^\s]+\s*)+")

# 已连上的企微长连接登记表：tenant_id -> (event_loop, websocket)
_CONNS_LOCK = threading.Lock()
_CONNS: dict[str, tuple[asyncio.AbstractEventLoop, Any]] = {}


def ws_url() -> str:
    return os.getenv("WECOM_BOT_WS_URL", DEFAULT_WECOM_WS_URL).strip() or DEFAULT_WECOM_WS_URL


def build_frame(cmd: str, body: dict[str, Any] | None = None, *, req_id: str | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "cmd": cmd,
        "headers": {"req_id": req_id or uuid.uuid4().hex},
    }
    if body is not None:
        frame["body"] = body
    return frame


def build_subscribe_frame(bot_id: str, bot_secret: str) -> dict[str, Any]:
    return build_frame("aibot_subscribe", {"bot_id": bot_id, "secret": bot_secret})


def build_respond_frame(req_id: str, content: str) -> dict[str, Any]:
    return build_frame(
        "aibot_respond_msg",
        {"msgtype": "markdown", "markdown": {"content": content}},
        req_id=req_id,
    )


def build_proactive_frame(chat_or_user_id: str, content: str) -> dict[str, Any]:
    return build_frame(
        "aibot_send_msg",
        {"chatid": chat_or_user_id, "msgtype": "markdown", "markdown": {"content": content}},
    )


def parse_wecom_callback(frame: dict[str, Any]) -> dict[str, str] | None:
    """解析企微下推的 aibot_msg_callback 消息帧；非文本消息返回 None。"""
    if not isinstance(frame, dict) or frame.get("cmd") != "aibot_msg_callback":
        return None
    headers = frame.get("headers") if isinstance(frame.get("headers"), dict) else {}
    req_id = str(headers.get("req_id") or "")
    body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
    msg_type = str(body.get("msgtype") or "")

    text = ""
    if msg_type == "text" and isinstance(body.get("text"), dict):
        text = str(body["text"].get("content") or "")
    elif msg_type == "mixed" and isinstance(body.get("mixed"), dict):
        items = body["mixed"].get("msg_item") or []
        parts = [
            str(it.get("text", {}).get("content") or "")
            for it in items
            if isinstance(it, dict) and it.get("msgtype") == "text" and isinstance(it.get("text"), dict)
        ]
        text = " ".join(p for p in parts if p)
    elif msg_type in {"voice", "image"}:
        text = str(body.get(msg_type, {}).get("content") or "") if isinstance(body.get(msg_type), dict) else ""

    cleaned = _MENTION_PREFIX_RE.sub("", text).strip()
    if not cleaned:
        return None

    sender = body.get("from") if isinstance(body.get("from"), dict) else {}
    userid = str(sender.get("userid") or body.get("userid") or "").strip()
    display_name = str(sender.get("name") or body.get("from_name") or "").strip()
    chat_id = str(body.get("chatid") or userid).strip()
    if not userid:
        return None
    return {
        "req_id": req_id,
        "external_id": userid,
        "chat_id": chat_id,
        "display_name": display_name,
        "text": cleaned,
    }


def process_callback_frame(tenant_id: str, frame: dict[str, Any]) -> dict[str, Any] | None:
    """同步处理一帧回调消息并返回响应帧（便于单元测试离线验证协议）。"""
    parsed = parse_wecom_callback(frame)
    if parsed is None:
        return None
    reply = im_gateway.handle_incoming_message(
        "wecom",
        parsed["external_id"],
        parsed["text"],
        tenant_id_hint=tenant_id,
        display_name=parsed["display_name"],
    )
    return build_respond_frame(parsed["req_id"] or uuid.uuid4().hex, reply)


def send_proactive_message_sync(tenant_id: str, external_id: str, content: str) -> bool:
    """在同步线程（如定时推送/工单回复）里复用已建立的长连接发送企微消息。"""
    with _CONNS_LOCK:
        entry = _CONNS.get(tenant_id)
    if entry is None:
        logger.info(f"[WeCom] 租户 {tenant_id} 当前无活跃长连接，跳过主动推送")
        return False
    loop, ws = entry
    frame = json.dumps(build_proactive_frame(external_id, content), ensure_ascii=False)
    try:
        future = asyncio.run_coroutine_threadsafe(ws.send(frame), loop)
        future.result(timeout=10.0)
        return True
    except Exception as exc:
        logger.warning(f"[WeCom] 主动推送失败 ({tenant_id}/{external_id}): {exc}")
        return False


async def _heartbeat_loop(ws: Any, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_SECONDS)
            break
        except asyncio.TimeoutError:
            await ws.send(json.dumps(build_frame("ping"), ensure_ascii=False))


async def _run_tenant_client(tenant_id: str, im: SchoolIm, stop_event: asyncio.Event) -> None:
    import websockets

    backoff = 2.0
    url = ws_url()
    while not stop_event.is_set():
        hb_task: asyncio.Task[None] | None = None
        hb_stop = asyncio.Event()
        try:
            async with websockets.connect(url, ping_interval=None) as ws:
                await ws.send(json.dumps(build_subscribe_frame(im.wecom_bot_id, im.wecom_bot_secret), ensure_ascii=False))
                loop = asyncio.get_running_loop()
                with _CONNS_LOCK:
                    _CONNS[tenant_id] = (loop, ws)
                hb_task = asyncio.create_task(_heartbeat_loop(ws, hb_stop))
                backoff = 2.0
                logger.info(f"[WeCom] 租户 {tenant_id} 智能机器人长连接已建立")

                async for raw in ws:
                    if stop_event.is_set():
                        break
                    try:
                        frame = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(frame, dict):
                        continue
                    cmd = str(frame.get("cmd") or "")
                    if cmd in {"pong", "aibot_subscribe_resp"}:
                        continue
                    resp_frame = await asyncio.to_thread(process_callback_frame, tenant_id, frame)
                    if resp_frame is not None:
                        await ws.send(json.dumps(resp_frame, ensure_ascii=False))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"[WeCom] 租户 {tenant_id} 长连接断开，{backoff:.0f}s 后重连: {exc}")
        finally:
            hb_stop.set()
            if hb_task is not None:
                hb_task.cancel()
            with _CONNS_LOCK:
                _CONNS.pop(tenant_id, None)

        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(60.0, backoff * 2.0)


class WeComBotManager:
    """在 FastAPI 生命周期内托管各校企微机器人长连接协程。"""

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> int:
        if os.getenv("WECOM_BOT_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
            return 0
        targets: list[tuple[str, SchoolIm]] = [
            (tid, cfg) for tid, cfg in list_configured_im_tenants() if cfg.has_wecom
        ]
        # 兼容单租户仅在 .env 里配平台级 WECOM_BOT_ID 的部署方式
        env_im = school_im(GLOBAL_TENANT)
        if env_im.has_wecom and not any(tid == GLOBAL_TENANT for tid, _ in targets):
            targets.append((GLOBAL_TENANT, env_im))

        for tid, cfg in targets:
            task = asyncio.create_task(_run_tenant_client(tid, cfg, self._stop), name=f"wecom-bot-{tid}")
            self._tasks.append(task)
        return len(self._tasks)

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()


manager = WeComBotManager()
