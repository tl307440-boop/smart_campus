"""企业微信扫码登录：服务器替学生打开学校 CAS「企业微信」按钮指向的扫码页，把二维码转给学生，轮询扫码结果。

和 open.work.weixin.qq.com 扫码页自己的前端脚本走同一套接口：
  1. GET wwopen/sso/qrConnect（就是 CAS 按钮上的链接），页面里带本次二维码的 key；
  2. 二维码图片是 wwopen/sso/qrImg?key=…；
  3. GET wwopen/sso/l/qrConnect 长轮询（JSONP），状态 NEVER → ING（已扫）→ SUCC（已确认，带 auth_code）；
  4. auth_code 交回 CAS 的 redirect_uri 换登录态，见 ZhengfangClient.wecom_login。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
from jw_zhengfang import JwError
from outbound import USER_AGENT, check_url, ssl_context

HOST = "open.work.weixin.qq.com"
# 企业微信那边一次长轮询最多挂 20 秒左右
POLL_TIMEOUT = httpx.Timeout(10.0, read=30.0)
_KEY = re.compile(r"qrImg\?key=([0-9a-zA-Z]+)")
_JSONP = re.compile(r"^\s*[\w$]+\((.*)\)\s*;?\s*$", re.S)
STATUS = {
    "QRCODE_SCAN_NEVER": "waiting",
    "QRCODE_SCAN_ING": "scanned",
    "QRCODE_SCAN_SUCC": "confirmed",
    "QRCODE_SCAN_FAIL": "cancelled",
    "QRCODE_SCAN_ERR": "expired",
}


@dataclass
class QrLogin:
    key: str
    appid: str
    redirect_uri: str
    state: str
    status: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _client(timeout: httpx.Timeout | float = 20.0, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        verify=ssl_context(),
        headers={"User-Agent": USER_AGENT},
        transport=transport,
        event_hooks={"request": [lambda request: check_url(str(request.url), frozenset({HOST}))]},
    )


def start(link: str, transport: httpx.BaseTransport | None = None) -> tuple[QrLogin, bytes]:
    """打开扫码页，返回这次扫码的上下文和二维码 PNG。"""
    query = dict(parse_qsl(urlsplit(link).query))
    with _client(transport=transport) as client:
        page = client.get(link)
        match = _KEY.search(page.text)
        if match is None:
            raise JwError("企业微信扫码页打不开，请稍后再试，或改用账号密码绑定")
        image = client.get(f"https://{HOST}/wwopen/sso/qrImg", params={"key": match.group(1)})
    if not image.headers.get("content-type", "").startswith("image/"):
        raise JwError("企业微信二维码没拿到，请稍后再试")
    qr = QrLogin(match.group(1), query.get("appid", ""), query.get("redirect_uri", ""), query.get("state", ""))
    return qr, image.content


def poll(qr: QrLogin, transport: httpx.BaseTransport | None = None) -> tuple[str, str]:
    """等一轮扫码状态，返回 (waiting|scanned|confirmed|cancelled|expired, auth_code)。"""
    params: dict[str, Any] = {
        "key": qr.key,
        "redirect_uri": qr.redirect_uri,
        "appid": qr.appid,
        "lastStatus": next((k for k, v in STATUS.items() if v == qr.status), ""),
        "callback": "jsonpCallback",
    }
    with _client(POLL_TIMEOUT, transport) as client:
        try:
            resp = client.get(f"https://{HOST}/wwopen/sso/l/qrConnect", params=params)
        except httpx.ReadTimeout:
            return qr.status or "waiting", ""
    match = _JSONP.match(resp.text)
    try:
        data = json.loads(match.group(1) if match else resp.text)
    except ValueError as exc:
        raise JwError("企业微信返回的扫码状态看不懂，请刷新二维码再试") from exc
    return STATUS.get(str(data.get("status")), qr.status or "waiting"), str(data.get("auth_code") or "")
