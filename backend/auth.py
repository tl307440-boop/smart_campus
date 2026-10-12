"""令牌校验：验签 Next 侧签发的访问令牌，并把身份注入路由。

令牌由 frontend/lib/auth/jwt.ts 用 jose 以 HS256 签发，两端共用 AUTH_JWT_SECRET。
改动 issuer 或 claim 名字必须同步修改那个文件，否则登录后所有接口都会 401。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated

import jwt
from env_config import load_backend_env
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from workspace import embed_settings

load_backend_env()

JWT_ISSUER = "zhiyue-campus"
JWT_ALGORITHM = "HS256"

# 能增删知识库的角色，与前端 lib/auth/session.ts 的 canManageKnowledge 保持一致
KNOWLEDGE_MANAGER_ROLES = frozenset({"STAFF", "ADMIN"})

# 嵌入挂件的访客。令牌由 frontend/app/api/embed/[id]/token 签发，额外带 eid（挂件 id）
GUEST_ROLE = "GUEST"

# auto_error=False：HTTPBearer 默认在缺少请求头时抛 403，而前端把 401 当作
# 「重新登录」的信号。这里自己处理，保证 401=没身份、403=权限不够。
_bearer = HTTPBearer(description="Next 登录后签发的访问令牌", auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    """令牌里携带的身份。字段与 AccessTokenClaims 一一对应。"""

    id: str
    tenant_id: str
    student_no: str
    name: str
    role: str
    embed_id: str = ""

    @property
    def can_manage_knowledge(self) -> bool:
        return self.role in KNOWLEDGE_MANAGER_ROLES

    @property
    def is_guest(self) -> bool:
        return self.role == GUEST_ROLE


def jwt_secret() -> str:
    """缺少密钥时直接报错，避免服务在「谁都能进」的状态下起来。"""
    secret = os.getenv("AUTH_JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "缺少 AUTH_JWT_SECRET，无法校验访问令牌。"
            "请在 backend/.env 填入与前端 frontend/.env.local 相同的值。"
        )
    return secret


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iss", "sub"]},
        )
    except InvalidTokenError as exc:
        # 过期、签名不符、issuer 不对、缺必需 claim 都归为「没有有效身份」
        raise _unauthorized("登录状态已失效，请重新登录") from exc


def _read_claim(payload: dict, key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """任何已登录用户都能通过。挂在需要身份的路由上。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("缺少访问令牌")

    payload = _decode(credentials.credentials)

    user_id = _read_claim(payload, "sub")
    tenant_id = _read_claim(payload, "tid")
    student_no = _read_claim(payload, "sno")
    name = _read_claim(payload, "name")
    role = _read_claim(payload, "role")
    if not (user_id and tenant_id and student_no and name and role):
        raise _unauthorized("令牌缺少必要信息，请重新登录")

    embed_id = ""
    if role == GUEST_ROLE:
        embed_id = _read_claim(payload, "eid") or ""
        embed = embed_settings(embed_id) if embed_id else None
        # 挂件被停用或改挂到别的学校后，已发出去的访客令牌立即失效（最多滞后一个缓存周期）
        if embed is None or not embed.enabled or embed.tenant_id != tenant_id:
            raise _unauthorized("该聊天挂件已停用")

    return CurrentUser(
        id=user_id,
        tenant_id=tenant_id,
        student_no=student_no,
        name=name,
        role=role,
        embed_id=embed_id,
    )


async def member(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    """登录的本校成员。挂件访客只能问答，不能碰记忆、文档列表和自带密钥。"""
    if user.is_guest:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="访客无权访问")
    return user


async def knowledge_manager(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    """只有教职工与管理员能改知识库。"""
    if not user.can_manage_knowledge:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要教职工或管理员权限",
        )
    return user


def scoped_thread_id(user: CurrentUser, thread_id: str) -> str:
    """把会话 id 收进用户命名空间。

    thread_id 由浏览器生成，不加前缀的话，拿到别人的 id 就能读到别人的对话历史。
    """
    return f"{user.tenant_id}:{user.id}:{thread_id}"


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
MemberDep = Annotated[CurrentUser, Depends(member)]
KnowledgeManagerDep = Annotated[CurrentUser, Depends(knowledge_manager)]
