"""服务端替学校、学生访问外部站点（教务、CAS、学校官网、高德、博查）时共用的两件事。

证书：不少学校站点只发了叶子证书、没带中间证书，浏览器会自己补，Python 不会。
把缺的中间证书放进 backend/certs/*.pem，和 certifi 的根证书合成一个包使用，而不是关掉校验。

地址：只放行解析到公网的地址，防止被当成探测内网的跳板。
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
import tempfile
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import certifi

CERT_DIR = Path(__file__).resolve().parent / "certs"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


class UnsafeUrlError(ValueError):
    """地址不合法、不在允许的主机里，或解析到内网。"""


@lru_cache(maxsize=1)
def ca_bundle() -> str:
    """certifi 根证书 + backend/certs 里补的中间证书，返回合并后的文件路径。"""
    extra = sorted(CERT_DIR.glob("*.pem")) if CERT_DIR.is_dir() else []
    if not extra:
        return certifi.where()
    parts = [Path(certifi.where()).read_text(encoding="utf-8")]
    parts += [path.read_text(encoding="utf-8") for path in extra]
    bundle = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False, encoding="utf-8")
    with bundle:
        bundle.write("\n".join(parts))
    return bundle.name


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_bundle())


def _host_is_public(host: str, port: int) -> bool:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"解析不了 {host}") from exc
    return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def check_url(url: str, allowed_hosts: frozenset[str] | None = None) -> None:
    """只允许 http/https、不带账号密码、（给了白名单时）主机在白名单里、解析到公网。"""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeUrlError("只支持 http/https 网址")
    if parts.username or parts.password:
        raise UnsafeUrlError("网址里不能带账号密码")
    host = parts.hostname.lower()
    if allowed_hosts is not None and host not in allowed_hosts:
        raise UnsafeUrlError(f"不允许访问 {host}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if not _host_is_public(host, port):
        raise UnsafeUrlError("不允许访问内网或保留地址")
