"""把一个网页抓下来转成纯文本，供知识库导入（学校官网通知、教务公告等）。

这是服务端替用户发请求，最大的风险是 SSRF：有人填 http://127.0.0.1:6379
或云厂商元数据地址 http://169.254.169.254 来探内网。所以：
  - 只允许 http/https，端口只放 80/443；
  - 解析域名后，任何一个地址落在内网 / 回环 / 链路本地 / 保留段都拒绝；
  - 不让 httpx 自动跟随跳转，每一跳都重新校验；
  - 限制超时、正文体积和内容类型。
残余风险：校验与连接之间的 DNS 重绑定，需要更强隔离时请把 API 放进无内网出口的网络。
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from logging_config import get_logger

logger = get_logger(__name__)

MAX_REDIRECTS = 3
MAX_BYTES = 3 * 1024 * 1024
TIMEOUT_SECONDS = 10.0
_ALLOWED_PORTS = {80, 443}
_ALLOWED_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
_NOISE_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe")
_BLANK_LINES = re.compile(r"\n{3,}")


class UnsafeUrlError(ValueError):
    """地址不合法或指向内网。"""


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    text: str


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeUrlError("只支持 http/https 网址")
    if parts.username or parts.password:
        raise UnsafeUrlError("网址里不能带账号密码")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in _ALLOWED_PORTS:
        raise UnsafeUrlError("只允许 80/443 端口")
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"域名无法解析: {parts.hostname}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise UnsafeUrlError("不允许访问内网或保留地址")


def _read_capped(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_BYTES:
            raise ValueError(f"网页超过 {MAX_BYTES // (1024 * 1024)} MB 上限")
        chunks.append(chunk)
    return b"".join(chunks)


def html_to_text(html: str) -> tuple[str, str]:
    """去掉导航、脚本等噪声，优先取 <article>/<main> 的正文。返回 (标题, 正文)。"""
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else ""
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = root.get_text("\n", strip=True)
    return title, _BLANK_LINES.sub("\n\n", text).strip()


def fetch_page(url: str, client: httpx.Client | None = None) -> WebPage:
    """抓取网页正文。

    Args:
        url: 用户填写的网址
        client: 测试时注入；默认新建一个不跟随跳转的客户端

    Returns:
        WebPage(url=最终地址, title=标题, text=正文)

    Example:
        >>> fetch_page("https://www.example.edu.cn/notice/1.html")  # doctest: +SKIP
    """
    logger.info(f"[WebImport] 开始抓取: {url}")
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
    current = url.strip()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _check_url(current)
            with http.stream("GET", current, headers={"User-Agent": "ZhiyueCampusBot/1.0"}) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location", "")
                    if not location:
                        raise ValueError("跳转缺少目标地址")
                    current = urljoin(current, location)
                    continue
                if resp.status_code != 200:
                    raise ValueError(f"网页返回 {resp.status_code}")
                content_type = resp.headers.get("content-type", "").lower()
                if not content_type.startswith(_ALLOWED_TYPES):
                    raise ValueError("只支持导入网页或纯文本，PDF 请下载后上传")
                body = _read_capped(resp)
                charset = resp.charset_encoding or "utf-8"
                break
        else:
            raise ValueError("跳转次数过多")
    finally:
        if owned:
            http.close()

    raw = body.decode(charset, errors="replace")
    if content_type.startswith("text/plain"):
        title, text = "", raw.strip()
    else:
        title, text = html_to_text(raw)
    if not text:
        raise ValueError("网页里没有可导入的正文")
    logger.info(f"[WebImport] 抓取完成: {current}，{len(text)} 字")
    return WebPage(url=current, title=title, text=text)
