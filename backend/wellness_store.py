"""情感陪伴与解压伴学存储层（微待办拆解 / 脱敏情绪轨迹手账 / 减压伴学舱状态）。

主路径走 PostgreSQL（表结构由 frontend/prisma/schema.prisma 迁移生成）；
未配置 DATABASE_URL 时自动落到本地 SQLite（`WELLNESS_SQLITE_PATH`），
保证本地单测与独立 stdio MCP 子进程之间也能真实共享状态。
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from env_config import load_backend_env
from logging_config import get_logger

logger = get_logger(__name__)
load_backend_env()

AMBIENT_SOUNDS = frozenset({"none", "rain", "library", "night_fire", "waves"})
BREATHING_MODES = frozenset({"none", "4-7-8", "box"})
PET_MOODS = frozenset({"normal", "listening", "cheering", "night_care", "relaxed"})
PRIORITIES = frozenset({"high", "medium", "low"})

_CONNECT_TIMEOUT_SECONDS = 3
_SQLITE_LOCK = threading.Lock()
_DEFAULT_TEST_SQLITE = os.path.join(tempfile.gettempdir(), f"zhiyue_wellness_{os.getpid()}.sqlite3")

# 隐私脱敏：情绪轨迹里允许保留日常烦恼/考试/学业压力，但必须抹掉身份证、手机号、密钥、邮箱等 PII
_PII_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{17}[\dXx]\b"), "[证件号]"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[邮箱]"),
    (re.compile(r"(sk-[A-Za-z0-9]{10,})"), "[密钥]"),
)


def sanitize_text(text: str, *, max_chars: int = 500) -> str:
    cleaned = " ".join((text or "").split())
    for pat, repl in _PII_PATTERNS:
        cleaned = pat.sub(repl, cleaned)
    return cleaned[:max_chars]


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def _sqlite_path() -> str:
    configured = os.getenv("WELLNESS_SQLITE_PATH", "").strip()
    if configured:
        return configured
    if os.getenv("PYTEST_CURRENT_TEST"):
        return _DEFAULT_TEST_SQLITE
    root = Path(__file__).resolve().parent.parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return str(root / "wellness_local.sqlite3")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: Any) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)


def _pg_connect() -> psycopg.Connection:
    return psycopg.connect(_database_url(), connect_timeout=_CONNECT_TIMEOUT_SECONDS)


def _sqlite_connect() -> sqlite3.Connection:
    path = _sqlite_path()
    conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS companion_todos (
            id TEXT PRIMARY KEY,
            tenantId TEXT NOT NULL,
            userId TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            estMinutes INTEGER NOT NULL DEFAULT 20,
            priority TEXT NOT NULL DEFAULT 'medium',
            dueLabel TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'mcp_decompose',
            status TEXT NOT NULL DEFAULT 'pending',
            completedAt TEXT,
            createdAt TEXT NOT NULL,
            updatedAt TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_todos_user ON companion_todos(tenantId, userId, status, createdAt);

        CREATE TABLE IF NOT EXISTS mood_journals (
            id TEXT PRIMARY KEY,
            tenantId TEXT NOT NULL,
            userId TEXT NOT NULL,
            mood TEXT NOT NULL,
            energy INTEGER NOT NULL DEFAULT 5,
            stressor TEXT NOT NULL DEFAULT '日常',
            summary TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'mcp_journal',
            followupAt TEXT,
            followupNote TEXT NOT NULL DEFAULT '',
            followupSent INTEGER NOT NULL DEFAULT 0,
            createdAt TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_journals_user ON mood_journals(tenantId, userId, createdAt);

        CREATE TABLE IF NOT EXISTS companion_states (
            id TEXT PRIMARY KEY,
            tenantId TEXT NOT NULL,
            userId TEXT NOT NULL,
            ambientSound TEXT NOT NULL DEFAULT 'none',
            breathingMode TEXT NOT NULL DEFAULT 'none',
            focusMinutes INTEGER NOT NULL DEFAULT 25,
            nightMode INTEGER NOT NULL DEFAULT 0,
            petMood TEXT NOT NULL DEFAULT 'normal',
            note TEXT NOT NULL DEFAULT '',
            updatedAt TEXT NOT NULL,
            UNIQUE(tenantId, userId)
        );
        """
    )
    return conn


def _todo_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "title": str(row[1]),
        "detail": str(row[2] or ""),
        "est_minutes": int(row[3] or 20),
        "priority": str(row[4] or "medium"),
        "due_label": str(row[5] or ""),
        "source": str(row[6] or "mcp_decompose"),
        "status": str(row[7] or "pending"),
        "completed_at": _to_iso(row[8]),
        "created_at": _to_iso(row[9]),
        "updated_at": _to_iso(row[10]),
    }


def _journal_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "mood": str(row[1] or "平静"),
        "energy": int(row[2] or 5),
        "stressor": str(row[3] or "日常"),
        "summary": str(row[4] or ""),
        "source": str(row[5] or "mcp_journal"),
        "followup_at": _to_iso(row[6]),
        "followup_note": str(row[7] or ""),
        "followup_sent": bool(row[8]),
        "created_at": _to_iso(row[9]),
    }


def _default_cabin() -> dict[str, Any]:
    return {
        "ambient_sound": "none",
        "breathing_mode": "none",
        "focus_minutes": 25,
        "night_mode": False,
        "pet_mood": "normal",
        "note": "",
        "updated_at": "",
    }


# ============================================================
# 1. 压力拆解微待办
# ============================================================


def add_todos(
    tenant_id: str,
    user_id: str,
    tasks: list[Any],
    *,
    source: str = "mcp_decompose",
) -> list[dict[str, Any]]:
    """批量写入微待办（单次最多 8 项，每项建议 15~30 分钟）。"""
    if not tenant_id or not user_id or not tasks:
        return []
    now = _utc_now()
    prepared: list[tuple[str, str, str, int, str, str]] = []
    for raw in tasks[:8]:
        if isinstance(raw, str):
            title, detail, est, prio, due = raw.strip(), "", 20, "medium", ""
        elif isinstance(raw, dict):
            title = str(raw.get("title") or "").strip()
            detail = str(raw.get("detail") or "").strip()
            try:
                est = max(5, min(120, int(raw.get("est_minutes") or 20)))
            except (TypeError, ValueError):
                est = 20
            prio_raw = str(raw.get("priority") or "medium").strip().lower()
            prio = prio_raw if prio_raw in PRIORITIES else "medium"
            due = str(raw.get("due_label") or "").strip()[:40]
        else:
            continue
        title = sanitize_text(title, max_chars=100)
        if not title:
            continue
        prepared.append((uuid.uuid4().hex, title, sanitize_text(detail, max_chars=240), est, prio, due))

    if not prepared:
        return []

    src = source[:32]
    created: list[dict[str, Any]] = []
    if _database_url():
        with _pg_connect() as conn, conn.cursor() as cur:
            for tid, title, detail, est, prio, due in prepared:
                cur.execute(
                    """
                    INSERT INTO companion_todos
                        ("id", "tenantId", "userId", "title", "detail", "estMinutes", "priority", "dueLabel", "source", "status", "createdAt", "updatedAt")
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
                    RETURNING "id", "title", "detail", "estMinutes", "priority", "dueLabel", "source", "status", "completedAt", "createdAt", "updatedAt"
                    """,
                    (tid, tenant_id, user_id, title, detail, est, prio, due, src, now.replace(tzinfo=None), now.replace(tzinfo=None)),
                )
                row = cur.fetchone()
                if row:
                    created.append(_todo_dict(row))
            conn.commit()
    else:
        iso = now.isoformat()
        with _SQLITE_LOCK, _sqlite_connect() as conn:
            for tid, title, detail, est, prio, due in prepared:
                conn.execute(
                    """
                    INSERT INTO companion_todos
                        (id, tenantId, userId, title, detail, estMinutes, priority, dueLabel, source, status, completedAt, createdAt, updatedAt)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)
                    """,
                    (tid, tenant_id, user_id, title, detail, est, prio, due, src, iso, iso),
                )
                created.append({
                    "id": tid, "title": title, "detail": detail, "est_minutes": est,
                    "priority": prio, "due_label": due, "source": src, "status": "pending",
                    "completed_at": "", "created_at": iso, "updated_at": iso,
                })
            conn.commit()
    return created


def list_todos(
    tenant_id: str,
    user_id: str,
    *,
    status: str = "all",
    limit: int = 40,
) -> list[dict[str, Any]]:
    if not tenant_id or not user_id:
        return []
    lim = max(1, min(100, int(limit)))
    status_filter = status if status in {"pending", "done"} else ""

    if _database_url():
        with _pg_connect() as conn, conn.cursor() as cur:
            if status_filter:
                cur.execute(
                    'SELECT "id", "title", "detail", "estMinutes", "priority", "dueLabel", "source", "status", "completedAt", "createdAt", "updatedAt" '
                    'FROM companion_todos WHERE "tenantId" = %s AND "userId" = %s AND "status" = %s '
                    'ORDER BY "createdAt" DESC LIMIT %s',
                    (tenant_id, user_id, status_filter, lim),
                )
            else:
                cur.execute(
                    'SELECT "id", "title", "detail", "estMinutes", "priority", "dueLabel", "source", "status", "completedAt", "createdAt", "updatedAt" '
                    'FROM companion_todos WHERE "tenantId" = %s AND "userId" = %s '
                    'ORDER BY CASE WHEN "status" = \'pending\' THEN 0 ELSE 1 END, "createdAt" DESC LIMIT %s',
                    (tenant_id, user_id, lim),
                )
            return [_todo_dict(r) for r in cur.fetchall()]

    with _SQLITE_LOCK, _sqlite_connect() as conn:
        if status_filter:
            rows = conn.execute(
                "SELECT id, title, detail, estMinutes, priority, dueLabel, source, status, completedAt, createdAt, updatedAt "
                "FROM companion_todos WHERE tenantId = ? AND userId = ? AND status = ? ORDER BY createdAt DESC LIMIT ?",
                (tenant_id, user_id, status_filter, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, detail, estMinutes, priority, dueLabel, source, status, completedAt, createdAt, updatedAt "
                "FROM companion_todos WHERE tenantId = ? AND userId = ? "
                "ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, createdAt DESC LIMIT ?",
                (tenant_id, user_id, lim),
            ).fetchall()
        return [_todo_dict(tuple(r)) for r in rows]


def toggle_todo(
    tenant_id: str,
    user_id: str,
    todo_id: str,
    *,
    done: bool | None = None,
) -> dict[str, Any] | None:
    if not tenant_id or not user_id or not todo_id:
        return None
    now = _utc_now()

    if _database_url():
        with _pg_connect() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT "status" FROM companion_todos WHERE "id" = %s AND "tenantId" = %s AND "userId" = %s',
                (todo_id, tenant_id, user_id),
            )
            cur_row = cur.fetchone()
            if cur_row is None:
                return None
            target_done = (cur_row[0] != "done") if done is None else bool(done)
            new_status = "done" if target_done else "pending"
            comp_at = now.replace(tzinfo=None) if target_done else None
            cur.execute(
                """
                UPDATE companion_todos
                SET "status" = %s, "completedAt" = %s, "updatedAt" = %s
                WHERE "id" = %s AND "tenantId" = %s AND "userId" = %s
                RETURNING "id", "title", "detail", "estMinutes", "priority", "dueLabel", "source", "status", "completedAt", "createdAt", "updatedAt"
                """,
                (new_status, comp_at, now.replace(tzinfo=None), todo_id, tenant_id, user_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _todo_dict(row) if row else None

    iso = now.isoformat()
    with _SQLITE_LOCK, _sqlite_connect() as conn:
        cur_row = conn.execute(
            "SELECT status FROM companion_todos WHERE id = ? AND tenantId = ? AND userId = ?",
            (todo_id, tenant_id, user_id),
        ).fetchone()
        if cur_row is None:
            return None
        target_done = (cur_row["status"] != "done") if done is None else bool(done)
        new_status = "done" if target_done else "pending"
        comp_at = iso if target_done else None
        conn.execute(
            "UPDATE companion_todos SET status = ?, completedAt = ?, updatedAt = ? WHERE id = ? AND tenantId = ? AND userId = ?",
            (new_status, comp_at, iso, todo_id, tenant_id, user_id),
        )
        row = conn.execute(
            "SELECT id, title, detail, estMinutes, priority, dueLabel, source, status, completedAt, createdAt, updatedAt "
            "FROM companion_todos WHERE id = ?",
            (todo_id,),
        ).fetchone()
        conn.commit()
        return _todo_dict(tuple(row)) if row else None


def delete_todo(tenant_id: str, user_id: str, todo_id: str) -> bool:
    if not tenant_id or not user_id or not todo_id:
        return False
    if _database_url():
        with _pg_connect() as conn, conn.cursor() as cur:
            cur.execute(
                'DELETE FROM companion_todos WHERE "id" = %s AND "tenantId" = %s AND "userId" = %s',
                (todo_id, tenant_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
    with _SQLITE_LOCK, _sqlite_connect() as conn:
        cur = conn.execute(
            "DELETE FROM companion_todos WHERE id = ? AND tenantId = ? AND userId = ?",
            (todo_id, tenant_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ============================================================
# 2. 脱敏情绪轨迹手账 & 跨天主动回访队列
# ============================================================


def add_mood_journal(
    tenant_id: str,
    user_id: str,
    *,
    mood: str,
    energy: int = 5,
    stressor: str = "日常",
    summary: str = "",
    source: str = "mcp_journal",
    followup_hours: int | float | None = None,
    followup_note: str = "",
) -> dict[str, Any] | None:
    if not tenant_id or not user_id:
        return None
    now = _utc_now()
    safe_mood = sanitize_text(mood or "平静", max_chars=24) or "平静"
    try:
        safe_energy = max(1, min(10, int(energy)))
    except (TypeError, ValueError):
        safe_energy = 5
    safe_stressor = sanitize_text(stressor or "日常", max_chars=32) or "日常"
    safe_summary = sanitize_text(summary or f"{safe_mood}（{safe_stressor}）", max_chars=500)
    safe_note = sanitize_text(followup_note, max_chars=200)

    followup_at: datetime | None = None
    if followup_hours is not None:
        try:
            hrs = max(1.0, min(168.0, float(followup_hours)))
            followup_at = now + timedelta(hours=hrs)
        except (TypeError, ValueError):
            followup_at = None
    elif safe_note:
        followup_at = now + timedelta(hours=18)

    jid = uuid.uuid4().hex
    src = source[:32]
    if _database_url():
        with _pg_connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mood_journals
                    ("id", "tenantId", "userId", "mood", "energy", "stressor", "summary", "source", "followupAt", "followupNote", "followupSent", "createdAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s)
                RETURNING "id", "mood", "energy", "stressor", "summary", "source", "followupAt", "followupNote", "followupSent", "createdAt"
                """,
                (
                    jid, tenant_id, user_id, safe_mood, safe_energy, safe_stressor, safe_summary, src,
                    followup_at.replace(tzinfo=None) if followup_at else None, safe_note, now.replace(tzinfo=None),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return _journal_dict(row) if row else None

    iso = now.isoformat()
    fu_iso = followup_at.isoformat() if followup_at else None
    with _SQLITE_LOCK, _sqlite_connect() as conn:
        conn.execute(
            """
            INSERT INTO mood_journals
                (id, tenantId, userId, mood, energy, stressor, summary, source, followupAt, followupNote, followupSent, createdAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (jid, tenant_id, user_id, safe_mood, safe_energy, safe_stressor, safe_summary, src, fu_iso, safe_note, iso),
        )
        conn.commit()
    return {
        "id": jid, "mood": safe_mood, "energy": safe_energy, "stressor": safe_stressor,
        "summary": safe_summary, "source": src, "followup_at": fu_iso or "",
        "followup_note": safe_note, "followup_sent": False, "created_at": iso,
    }


def list_mood_journals(
    tenant_id: str,
    user_id: str,
    *,
    days: int = 14,
    limit: int = 30,
) -> list[dict[str, Any]]:
    if not tenant_id or not user_id:
        return []
    since = _utc_now() - timedelta(days=max(1, min(90, int(days))))
    lim = max(1, min(100, int(limit)))

    if _database_url():
        with _pg_connect() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT "id", "mood", "energy", "stressor", "summary", "source", "followupAt", "followupNote", "followupSent", "createdAt" '
                'FROM mood_journals WHERE "tenantId" = %s AND "userId" = %s AND "createdAt" >= %s '
                'ORDER BY "createdAt" DESC LIMIT %s',
                (tenant_id, user_id, since.replace(tzinfo=None), lim),
            )
            return [_journal_dict(r) for r in cur.fetchall()]

    with _SQLITE_LOCK, _sqlite_connect() as conn:
        rows = conn.execute(
            "SELECT id, mood, energy, stressor, summary, source, followupAt, followupNote, followupSent, createdAt "
            "FROM mood_journals WHERE tenantId = ? AND userId = ? AND createdAt >= ? "
            "ORDER BY createdAt DESC LIMIT ?",
            (tenant_id, user_id, since.isoformat(), lim),
        ).fetchall()
        return [_journal_dict(tuple(r)) for r in rows]


def delete_mood_journal(tenant_id: str, user_id: str, journal_id: str) -> bool:
    if not tenant_id or not user_id or not journal_id:
        return False
    if _database_url():
        with _pg_connect() as conn, conn.cursor() as cur:
            cur.execute(
                'DELETE FROM mood_journals WHERE "id" = %s AND "tenantId" = %s AND "userId" = %s',
                (journal_id, tenant_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
    with _SQLITE_LOCK, _sqlite_connect() as conn:
        cur = conn.execute(
            "DELETE FROM mood_journals WHERE id = ? AND tenantId = ? AND userId = ?",
            (journal_id, tenant_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def claim_due_followups(*, now: datetime | None = None, limit: int = 30) -> list[dict[str, Any]]:
    """原子领取到期尚未发送的主动回访事件，防止调度器重复发送。"""
    cutoff = now or _utc_now()
    lim = max(1, min(100, int(limit)))
    claimed: list[dict[str, Any]] = []

    if _database_url():
        cutoff_naive = cutoff.replace(tzinfo=None)
        with _pg_connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE mood_journals
                SET "followupSent" = true
                WHERE "id" IN (
                    SELECT "id" FROM mood_journals
                    WHERE "followupSent" = false AND "followupAt" IS NOT NULL AND "followupAt" <= %s
                    ORDER BY "followupAt" ASC
                    LIMIT %s
                )
                RETURNING "id", "tenantId", "userId", "mood", "stressor", "summary", "followupNote"
                """,
                (cutoff_naive, lim),
            )
            for row in cur.fetchall():
                claimed.append({
                    "id": str(row[0]), "tenant_id": str(row[1]), "user_id": str(row[2]),
                    "mood": str(row[3]), "stressor": str(row[4]),
                    "summary": str(row[5]), "followup_note": str(row[6] or ""),
                })
            conn.commit()
        return claimed

    cutoff_iso = cutoff.isoformat()
    with _SQLITE_LOCK, _sqlite_connect() as conn:
        rows = conn.execute(
            """
            SELECT id, tenantId, userId, mood, stressor, summary, followupNote
            FROM mood_journals
            WHERE followupSent = 0 AND followupAt IS NOT NULL AND followupAt <= ?
            ORDER BY followupAt ASC LIMIT ?
            """,
            (cutoff_iso, lim),
        ).fetchall()
        for r in rows:
            cur = conn.execute("UPDATE mood_journals SET followupSent = 1 WHERE id = ? AND followupSent = 0", (r["id"],))
            if cur.rowcount > 0:
                claimed.append({
                    "id": str(r["id"]), "tenant_id": str(r["tenantId"]), "user_id": str(r["userId"]),
                    "mood": str(r["mood"]), "stressor": str(r["stressor"]),
                    "summary": str(r["summary"]), "followup_note": str(r["followupNote"] or ""),
                })
        conn.commit()
    return claimed


# ============================================================
# 3. 减压伴学舱实时状态
# ============================================================


def get_cabin_state(tenant_id: str, user_id: str) -> dict[str, Any]:
    if not tenant_id or not user_id:
        return _default_cabin()
    if _database_url():
        with _pg_connect() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT "ambientSound", "breathingMode", "focusMinutes", "nightMode", "petMood", "note", "updatedAt" '
                'FROM companion_states WHERE "tenantId" = %s AND "userId" = %s',
                (tenant_id, user_id),
            )
            row = cur.fetchone()
    else:
        with _SQLITE_LOCK, _sqlite_connect() as conn:
            row = conn.execute(
                "SELECT ambientSound, breathingMode, focusMinutes, nightMode, petMood, note, updatedAt "
                "FROM companion_states WHERE tenantId = ? AND userId = ?",
                (tenant_id, user_id),
            ).fetchone()
    if not row:
        return _default_cabin()
    return {
        "ambient_sound": str(row[0] or "none"),
        "breathing_mode": str(row[1] or "none"),
        "focus_minutes": int(row[2] or 25),
        "night_mode": bool(row[3]),
        "pet_mood": str(row[4] or "normal"),
        "note": str(row[5] or ""),
        "updated_at": _to_iso(row[6]),
    }


def update_cabin_state(
    tenant_id: str,
    user_id: str,
    *,
    ambient_sound: str | None = None,
    breathing_mode: str | None = None,
    focus_minutes: int | None = None,
    night_mode: bool | None = None,
    pet_mood: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if not tenant_id or not user_id:
        return _default_cabin()
    current = get_cabin_state(tenant_id, user_id)
    sound = ambient_sound if ambient_sound in AMBIENT_SOUNDS else current["ambient_sound"]
    breath = breathing_mode if breathing_mode in BREATHING_MODES else current["breathing_mode"]
    try:
        mins = max(5, min(90, int(focus_minutes))) if focus_minutes is not None else current["focus_minutes"]
    except (TypeError, ValueError):
        mins = current["focus_minutes"]
    night = current["night_mode"] if night_mode is None else bool(night_mode)
    pmood = pet_mood if pet_mood in PET_MOODS else current["pet_mood"]
    safe_note = sanitize_text(note, max_chars=160) if note is not None else current["note"]
    now = _utc_now()

    if _database_url():
        now_naive = now.replace(tzinfo=None)
        with _pg_connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO companion_states
                    ("id", "tenantId", "userId", "ambientSound", "breathingMode", "focusMinutes", "nightMode", "petMood", "note", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ("tenantId", "userId")
                DO UPDATE SET
                    "ambientSound" = EXCLUDED."ambientSound",
                    "breathingMode" = EXCLUDED."breathingMode",
                    "focusMinutes" = EXCLUDED."focusMinutes",
                    "nightMode" = EXCLUDED."nightMode",
                    "petMood" = EXCLUDED."petMood",
                    "note" = EXCLUDED."note",
                    "updatedAt" = EXCLUDED."updatedAt"
                """,
                (uuid.uuid4().hex, tenant_id, user_id, sound, breath, mins, night, pmood, safe_note, now_naive),
            )
            conn.commit()
    else:
        iso = now.isoformat()
        with _SQLITE_LOCK, _sqlite_connect() as conn:
            conn.execute(
                """
                INSERT INTO companion_states
                    (id, tenantId, userId, ambientSound, breathingMode, focusMinutes, nightMode, petMood, note, updatedAt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenantId, userId)
                DO UPDATE SET
                    ambientSound = excluded.ambientSound,
                    breathingMode = excluded.breathingMode,
                    focusMinutes = excluded.focusMinutes,
                    nightMode = excluded.nightMode,
                    petMood = excluded.petMood,
                    note = excluded.note,
                    updatedAt = excluded.updatedAt
                """,
                (uuid.uuid4().hex, tenant_id, user_id, sound, breath, mins, int(night), pmood, safe_note, iso),
            )
            conn.commit()

    return {
        "ambient_sound": sound,
        "breathing_mode": breath,
        "focus_minutes": mins,
        "night_mode": night,
        "pet_mood": pmood,
        "note": safe_note,
        "updated_at": now.isoformat(),
    }


def build_companion_context(tenant_id: str, user_id: str) -> str:
    """组装注入陪伴型 Agent 的近况摘要（近期情绪走势 + 未完成微待办）。"""
    if not tenant_id or not user_id:
        return ""
    lines: list[str] = []
    try:
        journals = list_mood_journals(tenant_id, user_id, days=5, limit=5)
        if journals:
            avg_energy = round(sum(j["energy"] for j in journals) / len(journals), 1)
            recent = "; ".join(f"[{j['mood']}/能量{j['energy']}/{j['stressor']}] {j['summary'][:40]}" for j in journals[:3])
            lines.append(f"- 近 5 天情绪平均能量 {avg_energy}/10；近期记录：{recent}")
    except Exception as exc:
        logger.debug(f"[Wellness] 读取情绪近况失败: {exc}")

    try:
        pending = list_todos(tenant_id, user_id, status="pending", limit=5)
        if pending:
            todo_str = "；".join(
                f"{t['title']}（约{t['est_minutes']}分钟{',' + t['due_label'] if t['due_label'] else ''}）"
                for t in pending[:4]
            )
            lines.append(f"- 当前尚未完成的微待办（共 {len(pending)} 项）：{todo_str}")
    except Exception as exc:
        logger.debug(f"[Wellness] 读取微待办失败: {exc}")

    return "\n".join(lines)
