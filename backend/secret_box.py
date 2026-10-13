"""学校在「AI 服务」页填的模型密钥，库里只存密文。

AES-256-GCM，加密密钥由 AUTH_JWT_SECRET 经 HKDF-SHA256 派生。加密在 Next 侧
（frontend/lib/ai-config/secret-box.ts），两端的 salt / info / 密文格式必须逐字一致：
    "v1." + base64url(12 字节 nonce + 密文 + 16 字节 tag)
换了 AUTH_JWT_SECRET，旧密文全部解不开，学校要重新填一次密钥。
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = "v1."
_SALT = b"zhiyue-campus"
_INFO = b"tenant-ai-config:v1"
_NONCE_BYTES = 12


def _key() -> bytes:
    secret = os.getenv("AUTH_JWT_SECRET", "").strip()
    if not secret:
        raise ValueError("缺少 AUTH_JWT_SECRET，无法解密学校的模型密钥")
    return HKDF(algorithm=SHA256(), length=32, salt=_SALT, info=_INFO).derive(secret.encode())


def encrypt(plain: str) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    body = AESGCM(_key()).encrypt(nonce, plain.encode(), None)
    return PREFIX + base64.urlsafe_b64encode(nonce + body).decode().rstrip("=")


def decrypt(token: str) -> str:
    """空串原样返回（没填密钥）；格式不对或被篡改时抛 ValueError / InvalidTag。

    Example:
        >>> decrypt(encrypt("sk-abc"))
        'sk-abc'
    """
    if not token:
        return ""
    if not token.startswith(PREFIX):
        raise ValueError("密文格式不对")
    data = token[len(PREFIX):]
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    nonce, body = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    return AESGCM(_key()).decrypt(nonce, body, None).decode()
