"""后台自己的登录会话。密码核对现有用户表里的 Argon2id 散列，不另建账号。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

# 与 frontend/lib/auth/password.ts 的占位散列相同，账号不存在时也跑一次校验。
DUMMY_HASH = "$argon2id$v=19$m=19456,t=2,p=1$c21hcnRjYW1wdXNkdW1teQ$Rd1Y3jVBiPPYVsBpNRRYQXQrOZTMPFqmjLBLQZWZKvo"
SESSION_HOURS = 12
_hasher = PasswordHasher()


def password_ok(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except VerificationError:
        return False


def sign(payload: dict[str, Any], secret: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def unsign(token: str, secret: str) -> dict[str, Any] | None:
    body, dot, sig = token.rpartition(".")
    if not dot or not body:
        return None
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or float(data.get("exp") or 0) < time.time():
        return None
    return data
