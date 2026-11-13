"""QQ OneBot v11 协议适配器（Webhook 收消息 + HTTP API 发私聊/群消息）。

兼容 Lagrange.OneBot、NapCat、LLOneBot 等标准 OneBot v11 实现：
- 入站：POST /api/im/qq/event（支持 X-Signature HMAC-SHA1 验签与 Bearer 校验）；
- 解析：支持私聊与群聊 @机器人，同时兼容 CQ 码字符串与结构化消息段数组；
- 出站：调用 `{qq_onebot_url}/send_private_msg` 或 `/send_group_msg`。
"""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

import httpx
from logging_config import get_logger

logger = get_logger(__name__)

_CQ_AT_RE = re.compile(r"\[CQ:at,qq=(\d+|all)[^\]]*\]")
_CQ_TAG_RE = re.compile(r"\[CQ:[^\]]+\]")


def verify_signature(body: bytes, signature_header: str, secret: str, *, bearer_token: str = "") -> bool:
    """校验 OneBot v11 Webhook 签名。未配置 Token 时直接放行，已配置时校验 X-Signature 或 Bearer。"""
    cleaned_secret = (secret or "").strip()
    if not cleaned_secret:
        return True
    if signature_header:
        sig = signature_header.strip()
        if sig.lower().startswith("sha1="):
            sig = sig[5:]
        expected = hmac.new(cleaned_secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
        return hmac.compare_digest(sig.lower(), expected.lower())
    if bearer_token:
        token = bearer_token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return hmac.compare_digest(token, cleaned_secret)
    return False


def extract_plain_text(raw_message: Any, *, self_id: str = "") -> tuple[str, bool]:
    """从 OneBot 消息内容（CQ 字符串或消息段数组）提取纯文本与是否被 @机器人。"""
    mentioned_self = False
    if isinstance(raw_message, list):
        pieces: list[str] = []
        for seg in raw_message:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type") or "")
            data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            if seg_type == "at":
                qq = str(data.get("qq") or "")
                if self_id and qq == self_id:
                    mentioned_self = True
            elif seg_type == "text":
                t = str(data.get("text") or "")
                if t:
                    pieces.append(t)
        return " ".join("".join(pieces).split()), mentioned_self

    text = str(raw_message or "")
    for match in _CQ_AT_RE.finditer(text):
        if self_id and match.group(1) == self_id:
            mentioned_self = True
    cleaned = _CQ_TAG_RE.sub("", text)
    return " ".join(cleaned.split()), mentioned_self


def parse_onebot_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """解析 OneBot v11 上报事件；非用户消息（心跳/自己发的/群里未 @）返回 None。"""
    if not isinstance(payload, dict) or payload.get("post_type") != "message":
        return None
    self_id = str(payload.get("self_id") or "")
    user_id = str(payload.get("user_id") or "")
    if not user_id or (self_id and user_id == self_id):
        return None

    msg_type = str(payload.get("message_type") or "private")
    raw_msg = payload.get("message") if payload.get("message") is not None else payload.get("raw_message", "")
    text, mentioned_self = extract_plain_text(raw_msg, self_id=self_id)
    if not text:
        return None

    is_group = msg_type == "group"
    group_id = str(payload.get("group_id") or "") if is_group else ""
    # 群聊必须 @机器人 或 以快捷绑定/帮助/早报指令开头才响应，避免误触刷屏
    if is_group and not mentioned_self:
        lowered = text.lower()
        if not lowered.startswith(("绑定", "/bind", "早报", "/digest", "帮助", "/help", "小懒")):
            return None
        if lowered.startswith("小懒"):
            text = text[2:].lstrip(" ,，:：")
            if not text:
                return None

    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    display_name = str(sender.get("card") or sender.get("nickname") or "")
    return {
        "external_id": user_id,
        "display_name": display_name,
        "is_group": is_group,
        "group_id": group_id,
        "text": text,
    }


def _onebot_id(raw: str) -> int | str:
    return int(raw) if raw.isdigit() else raw


def _post_onebot(qq_url: str, qq_token: str, action: str, body: dict[str, Any]) -> bool:
    base = (qq_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return False
    headers = {"Content-Type": "application/json"}
    if qq_token:
        headers["Authorization"] = f"Bearer {qq_token}"
    try:
        resp = httpx.post(f"{base}/{action}", json=body, headers=headers, timeout=10.0)
        return resp.status_code < 400
    except Exception as exc:
        logger.warning(f"[QQ OneBot] 调用 {action} 失败: {exc}")
        return False


def send_private_message(qq_url: str, qq_token: str, user_id: str, text: str) -> bool:
    return _post_onebot(qq_url, qq_token, "send_private_msg", {"user_id": _onebot_id(user_id), "message": text})


def send_group_message(qq_url: str, qq_token: str, group_id: str, text: str, *, at_user_id: str = "") -> bool:
    msg = f"[CQ:at,qq={at_user_id}] {text}" if at_user_id else text
    return _post_onebot(qq_url, qq_token, "send_group_msg", {"group_id": _onebot_id(group_id), "message": msg})
