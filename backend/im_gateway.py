"""统一 IM 网关（Phase 3）：企业微信机器人 & QQ OneBot 共享对话与身份绑定桥接。

核心流程：
1. 学生在网页端「消息推送」生成一个 6 位一次性绑定码（有效期 10 分钟，存 Redis）；
2. 学生在企微/QQ 对机器人发送 `绑定 123456`，完成 `(tenantId, platform, externalId) -> userId` 绑定；
3. 绑定后的所有私聊/群内 @提问 复用 `graph_app` + `agent` + `rag` 主链路，并格式化为适配 IM 阅读的纯文本/Markdown。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

import chat_history
import redis
from logging_config import get_logger

logger = get_logger(__name__)

BIND_CODE_TTL = 600
VALID_PLATFORMS = ("wecom", "qq")
_BIND_CMD_RE = re.compile(r"^\s*(?:/?bind|绑定)\s+(\d{6})\s*$", re.IGNORECASE)
_UNBIND_CMD_RE = re.compile(r"^\s*(?:/?unbind|解绑|取消绑定)\s*$", re.IGNORECASE)
_DIGEST_CMD_RE = re.compile(r"^\s*(?:/?digest|早报|今日早报)\s*$", re.IGNORECASE)
_HELP_CMD_RE = re.compile(r"^\s*(?:/?help|帮助|菜单|\?)\s*$", re.IGNORECASE)
_DIGEST_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

_client: redis.Redis | None = None
_MEM_CODES: dict[str, tuple[float, dict[str, str]]] = {}
_MEM_LOCK = threading.Lock()


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
    return _client


def _code_key(code: str) -> str:
    return f"im:bind_code:{code}"


def create_bind_code(tenant_id: str, user_id: str, ttl: int = BIND_CODE_TTL) -> str:
    """生成 6 位绑定码，供学生在企微/QQ 对话框输入完成绑定。"""
    payload = {"tenant_id": tenant_id, "user_id": user_id}
    raw = json.dumps(payload, ensure_ascii=False)
    for _ in range(8):
        code = f"{secrets.randbelow(900000) + 100000}"
        stored = False
        try:
            r = _redis()
            if not r.exists(_code_key(code)):
                r.set(_code_key(code), raw, ex=ttl)
                stored = True
        except redis.RedisError:
            pass
        with _MEM_LOCK:
            hit = _MEM_CODES.get(code)
            if not hit or hit[0] <= time.monotonic():
                _MEM_CODES[code] = (time.monotonic() + ttl, payload)
                stored = True
        if stored:
            return code
    raise RuntimeError("生成绑定码失败，请稍后重试")


def _pop_bind_code(code: str) -> dict[str, str] | None:
    try:
        r = _redis()
        key = _code_key(code)
        raw = r.get(key)
        if raw:
            r.delete(key)
            with _MEM_LOCK:
                _MEM_CODES.pop(code, None)
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("tenant_id") and data.get("user_id"):
                return {"tenant_id": str(data["tenant_id"]), "user_id": str(data["user_id"])}
    except Exception:
        pass
    with _MEM_LOCK:
        hit = _MEM_CODES.pop(code, None)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    return None


@dataclass(frozen=True)
class ImBinding:
    id: str
    tenant_id: str
    user_id: str
    platform: str
    external_id: str
    display_name: str
    morning_digest: bool
    digest_time: str
    grade_alert: bool
    ticket_alert: bool
    news_keywords: str
    last_grade_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "platform": self.platform,
            "external_id": self.external_id,
            "display_name": self.display_name,
            "morning_digest": self.morning_digest,
            "digest_time": self.digest_time,
            "grade_alert": self.grade_alert,
            "ticket_alert": self.ticket_alert,
            "news_keywords": self.news_keywords,
        }


def _row_to_binding(row: tuple[Any, ...]) -> ImBinding:
    return ImBinding(
        id=str(row[0]),
        tenant_id=str(row[1]),
        user_id=str(row[2]),
        platform=str(row[3]),
        external_id=str(row[4]),
        display_name=str(row[5] or ""),
        morning_digest=bool(row[6]),
        digest_time=str(row[7] or "07:30"),
        grade_alert=bool(row[8]),
        ticket_alert=bool(row[9]),
        news_keywords=str(row[10] or ""),
        last_grade_hash=str(row[11] or ""),
    )


_SELECT_COLS = (
    '"id", "tenantId", "userId", "platform", "externalId", "displayName", '
    '"morningDigest", "digestTime", "gradeAlert", "ticketAlert", "newsKeywords", "lastGradeHash"'
)


def _connect():
    return chat_history._connect()


def consume_bind_code(
    code: str,
    platform: str,
    external_id: str,
    *,
    display_name: str = "",
) -> ImBinding:
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"不支持的平台: {platform}")
    ext_id = (external_id or "").strip()[:120]
    if not ext_id:
        raise ValueError("缺少外部账号标识")
    claim = _pop_bind_code(code.strip())
    if not claim:
        raise ValueError("绑定码不存在或已过期，请回网页端「工具 → 消息推送」重新生成 6 位数字码")

    tenant_id = claim["tenant_id"]
    user_id = claim["user_id"]
    binding_id = str(uuid.uuid4())
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO im_bindings ("id", "tenantId", "userId", "platform", "externalId", "displayName")
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT ("tenantId", "platform", "externalId")
                DO UPDATE SET
                    "userId" = EXCLUDED."userId",
                    "displayName" = CASE WHEN EXCLUDED."displayName" <> '' THEN EXCLUDED."displayName" ELSE im_bindings."displayName" END,
                    "updatedAt" = NOW()
                RETURNING {_SELECT_COLS}
                """,
                (binding_id, tenant_id, user_id, platform, ext_id, (display_name or "")[:80]),
            )
            row = cur.fetchone()
    if row is None:
        raise RuntimeError("保存 IM 绑定失败")
    return _row_to_binding(row)


def resolve_binding(platform: str, external_id: str, *, tenant_id_hint: str = "") -> ImBinding | None:
    if not chat_history.enabled():
        return None
    ext_id = (external_id or "").strip()
    if not ext_id:
        return None
    with _connect() as conn:
        with conn.cursor() as cur:
            if tenant_id_hint:
                cur.execute(
                    f'SELECT {_SELECT_COLS} FROM im_bindings '
                    'WHERE "platform" = %s AND "externalId" = %s AND "tenantId" = %s',
                    (platform, ext_id, tenant_id_hint),
                )
            else:
                cur.execute(
                    f'SELECT {_SELECT_COLS} FROM im_bindings '
                    'WHERE "platform" = %s AND "externalId" = %s '
                    'ORDER BY "updatedAt" DESC LIMIT 1',
                    (platform, ext_id),
                )
            row = cur.fetchone()
    return _row_to_binding(row) if row else None


def list_user_bindings(tenant_id: str, user_id: str) -> list[dict[str, Any]]:
    if not chat_history.enabled():
        return []
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT {_SELECT_COLS} FROM im_bindings WHERE "tenantId" = %s AND "userId" = %s ORDER BY "createdAt" ASC',
                (tenant_id, user_id),
            )
            rows = cur.fetchall()
    return [_row_to_binding(r).to_dict() for r in rows]


def update_binding_preferences(
    tenant_id: str,
    user_id: str,
    binding_id: str,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    sets: list[str] = ['"updatedAt" = NOW()']
    params: list[Any] = []
    if "morning_digest" in patch and patch["morning_digest"] is not None:
        sets.append('"morningDigest" = %s')
        params.append(bool(patch["morning_digest"]))
    if "digest_time" in patch and isinstance(patch["digest_time"], str):
        t = patch["digest_time"].strip()
        if not _DIGEST_TIME_RE.match(t):
            raise ValueError("推送时间格式需要为 HH:MM（如 07:30）")
        sets.append('"digestTime" = %s')
        params.append(t)
    if "grade_alert" in patch and patch["grade_alert"] is not None:
        sets.append('"gradeAlert" = %s')
        params.append(bool(patch["grade_alert"]))
    if "ticket_alert" in patch and patch["ticket_alert"] is not None:
        sets.append('"ticketAlert" = %s')
        params.append(bool(patch["ticket_alert"]))
    if "news_keywords" in patch and patch["news_keywords"] is not None:
        sets.append('"newsKeywords" = %s')
        params.append(str(patch["news_keywords"]).strip()[:200])

    if len(sets) == 1:
        raise ValueError("没有可更新的推送设置")

    params.extend([binding_id, tenant_id, user_id])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'UPDATE im_bindings SET {", ".join(sets)} '
                'WHERE "id" = %s AND "tenantId" = %s AND "userId" = %s '
                f'RETURNING {_SELECT_COLS}',
                tuple(params),
            )
            row = cur.fetchone()
    return _row_to_binding(row).to_dict() if row else None


def delete_binding(tenant_id: str, user_id: str, binding_id: str) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM im_bindings WHERE "id" = %s AND "tenantId" = %s AND "userId" = %s',
                (binding_id, tenant_id, user_id),
            )
            return cur.rowcount > 0


def delete_binding_by_external(tenant_id: str, platform: str, external_id: str) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM im_bindings WHERE "tenantId" = %s AND "platform" = %s AND "externalId" = %s',
                (tenant_id, platform, external_id),
            )
            return cur.rowcount > 0


def list_active_bindings(*, limit: int = 1000) -> list[ImBinding]:
    if not chat_history.enabled():
        return []
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT {_SELECT_COLS} FROM im_bindings ORDER BY "updatedAt" DESC LIMIT %s',
                (max(1, min(5000, int(limit))),),
            )
            rows = cur.fetchall()
    return [_row_to_binding(r) for r in rows]


def update_grade_hash(binding_id: str, grade_hash: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE im_bindings SET "lastGradeHash" = %s, "updatedAt" = NOW() WHERE "id" = %s',
                (grade_hash[:128], binding_id),
            )


# ============================================================
# 对话桥接与主动消息投递
# ============================================================


def format_im_answer(
    answer: str,
    tables: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    """将对话结果、表格与参考出处整理为适合 QQ / 企微阅读的消息正文。"""
    parts: list[str] = [(answer or "").strip() or "（暂时没有生成内容）"]
    for tbl in (tables or [])[:2]:
        cols = tbl.get("columns") or []
        rows = tbl.get("rows") or []
        if not cols or not rows:
            continue
        lines = [f"\n【{tbl.get('title') or '查询结果'}】"]
        for r in rows[:15]:
            row_text = " · ".join(f"{c['label']}: {r.get(c['key'], '')}" for c in cols if r.get(c["key"], ""))
            if row_text:
                lines.append(f"- {row_text}")
        total = int(tbl.get("total") or len(rows))
        if total > 15:
            lines.append(f"… 共 {total} 条，完整表格可在网页端查看")
        parts.append("\n".join(lines))

    cites: list[str] = []
    for src in (sources or [])[:3]:
        name = str(src.get("source") or "").strip()
        url = str(src.get("url") or "").strip()
        if name:
            cites.append(f"[{name}]({url})" if url else name)
    if cites:
        parts.append("\n参考来源：" + "；".join(cites))
    return "\n".join(parts).strip()


def _ask_campus_graph(tenant_id: str, user_id: str, thread_id: str, question: str) -> str:
    from graph_app import ask_question
    from knowledge import knowledge_base

    corpus = knowledge_base.short_corpus(tenant_id)
    result = ask_question(
        question=question,
        doc_content=corpus,
        strategy="auto",
        thread_id=thread_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    return format_im_answer(
        str(result.get("final_answer") or ""),
        result.get("tables"),
        result.get("sources"),
    )


def handle_incoming_message(
    platform: str,
    external_id: str,
    text: str,
    *,
    tenant_id_hint: str = "",
    display_name: str = "",
) -> str:
    """企微与 QQ 统一入站消息处理入口：指令路由 + 校园智能体问答。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return "发句话给我吧，比如 `帮助`、`早报` 或直接问课表/校园规定～"

    if _HELP_CMD_RE.match(cleaned):
        return (
            "你好，我是智悦校园助手！支持以下快捷指令：\n"
            "1. `绑定 123456` — 绑定你在网页端生成的 6 位绑定码\n"
            "2. `早报` — 立即获取今日课表、临近考试与匹配通知\n"
            "3. `解绑` — 解除当前端推送绑定\n"
            "也可以直接发自然语言提问（例如：明天高数在哪上 / 国家奖学金怎么评 / 查一下我的物理课件重点）。"
        )

    if match := _BIND_CMD_RE.match(cleaned):
        try:
            binding = consume_bind_code(
                match.group(1), platform, external_id, display_name=display_name
            )
            return (
                f"绑定成功！已关联学校 `{binding.tenant_id}`。\n"
                "现在可以直接在聊天框问我课表、成绩、通知或发 `早报` 试试，每天早上也会按偏好推送今日摘要。"
            )
        except Exception as exc:
            return f"绑定失败：{exc}"

    binding = resolve_binding(platform, external_id, tenant_id_hint=tenant_id_hint)
    if binding is None:
        return (
            "还没有绑定校园账号哦～\n"
            "请先登录智悦校园网页端，打开右侧「工具 → 消息推送」获取 6 位绑定码，然后发送：`绑定 123456`。"
        )

    if _UNBIND_CMD_RE.match(cleaned):
        delete_binding_by_external(binding.tenant_id, platform, external_id)
        return "已解除当前账号绑定，后续将不再推送早报或出分提醒。需要时可随时重新发送 `绑定 6位数字码`。"

    if _DIGEST_CMD_RE.match(cleaned):
        import im_scheduler

        return im_scheduler.build_morning_digest(
            binding.tenant_id, binding.user_id, date.today(), binding.news_keywords
        )

    thread_id = f"im:{binding.tenant_id}:{binding.user_id}:{platform}:{external_id}"
    try:
        return _ask_campus_graph(binding.tenant_id, binding.user_id, thread_id, cleaned)
    except Exception as exc:
        logger.warning(f"[IM] 处理 {platform}:{external_id} 问答失败: {exc}")
        return "校园助手暂时开小差了，请稍后再试或到网页端查看。"


def send_im_message(tenant_id: str, platform: str, external_id: str, text: str) -> bool:
    """统一出站投递入口：根据平台路由到 QQ OneBot 或企微长连接。"""
    from workspace import school_im

    im_cfg = school_im(tenant_id)
    if platform == "qq":
        if not im_cfg.has_qq:
            return False
        import qq_bot

        return qq_bot.send_private_message(im_cfg.qq_onebot_url, im_cfg.qq_onebot_token, external_id, text)

    if platform == "wecom":
        if not im_cfg.has_wecom:
            return False
        import wecom_bot

        return wecom_bot.send_proactive_message_sync(tenant_id, external_id, text)

    return False
