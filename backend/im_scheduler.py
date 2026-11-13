"""主动推送调度器（早报提醒 / 出分提醒 / HITL 人工工单回复通知）。

支持：
1. 每日定时早报：聚合 `today_card` 今日课表 + 近期考试倒计时 + 按专业/订阅词筛选的学校官网通知 + 伴学空间待复习闪卡提醒；
2. 出分提醒：对比正方教务成绩单稳定性散列，新科目或成绩变更时第一时间把新成绩和最新加权平均绩点推送给学生；
3. HITL 工单闭环通知：老师在后台回复或回流工单后，自动给发起求助的学生发企微/QQ 通知；
4. Redis 按日去重 & 兜底内存去重，保证定时轮询不重复打扰。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime
from typing import Any

import im_gateway
import jw_account
import redis
import school_tools
import today_card
from jw_zhengfang import JwAuthError, JwError, current_term
from logging_config import get_logger
from workspace import school_ai

logger = get_logger(__name__)

DIGEST_WINDOW_MINUTES = 20
GRADE_CHECK_INTERVAL_SECONDS = 1800
SCHEDULER_POLL_SECONDS = 60

_DEDUPE_LOCK = threading.Lock()
_LOCAL_DEDUPE: dict[str, float] = {}
_LOCAL_GRADES: dict[str, dict[str, str]] = {}


def _redis() -> redis.Redis:
    return today_card._redis()


def _acquire_once(key: str, ttl: int) -> bool:
    """在 Redis（或单进程内存）内抢占一次性触发权，返回 True 表示此前未发送。"""
    try:
        ok = _redis().set(key, "1", nx=True, ex=ttl)
        if ok is not None:
            return bool(ok)
    except redis.RedisError:
        pass
    now = time.monotonic()
    with _DEDUPE_LOCK:
        exp = _LOCAL_DEDUPE.get(key, 0.0)
        if exp > now:
            return False
        _LOCAL_DEDUPE[key] = now + ttl
        return True


def _split_keywords(raw: str) -> list[str]:
    return [tok.strip().lower() for tok in re.split(r"[\s,，、;；]+", raw or "") if tok.strip()]


def _student_major_keywords(tenant_id: str, user_id: str) -> list[str]:
    try:
        import memory_store

        if not memory_store.enabled():
            return []
        notes = memory_store.list_memories(tenant_id, user_id)
        found: list[str] = []
        for note in notes:
            if note.slot in {"major", "college"} and note.content:
                cleaned = re.sub(r"(?:我是|我在|所学专业是?|所在学院是?|专业[:：]?|学院[:：]?)", "", note.content).strip()
                if cleaned:
                    found.append(cleaned.lower())
        return found[:2]
    except Exception:
        return []


def _due_flashcard_count(tenant_id: str, user_id: str) -> int:
    try:
        import study_space

        return study_space.count_due_flashcards(tenant_id, user_id)
    except Exception:
        return 0


def build_morning_digest(
    tenant_id: str,
    user_id: str,
    today: date,
    news_keywords: str = "",
) -> str:
    """生成学生个人每日早报文本。"""
    lines: list[str] = [f"### 🌅 今日校园早报 · {today.isoformat()}"]

    # 1. 今日课表 + 考试
    try:
        card = today_card.build_card(tenant_id, user_id, today)
    except (JwAuthError, JwError, redis.RedisError) as exc:
        card = {"enabled": True, "bound": False, "error": str(exc)}

    if card.get("enabled") and card.get("bound"):
        week_label = f"第 {card['week']} 周" if card.get("week") else "教学周待定"
        lines[0] = f"### 🌅 今日校园早报 · {today.isoformat()} {card.get('weekday', '')}（{week_label}）"
        lessons = card.get("lessons") or []
        if lessons:
            lines.append(f"\n**今日课程（共 {len(lessons)} 节）**")
            for item in lessons[:8]:
                t_range = f"{item['start']}-{item['end']}" if item.get("start") and item.get("end") else f"第{item.get('periods', '')}节"
                room = f" @ {item['room']}" if item.get("room") else ""
                lines.append(f"- {t_range} · {item.get('course', '未命名课程')}{room}")
        else:
            lines.append("\n**今日课程**：今天没有排课，可安排自习或复习～")

        exams = [e for e in (card.get("exams") or []) if int(e.get("days_left", 99)) <= 14]
        if exams:
            lines.append("\n**临近考试（14 天内）**")
            for ex in exams[:3]:
                when = "今天" if ex["days_left"] == 0 else ("明天" if ex["days_left"] == 1 else f"{ex['days_left']} 天后")
                lines.append(f"- 【{when}】{ex['course']}（{ex['date']} {ex['start']} {ex['room']}）")
    elif card.get("enabled"):
        lines.append("\n提示：还没有绑定教务账号，去网页端「工具 → 教务账号」绑定后即可自动推送每日课表与出分提醒。")

    # 2. 按专业/订阅词筛选学校通知
    school = school_ai(tenant_id)
    if school.news_urls:
        try:
            news_items = school_tools.school_news(school.news_urls, keyword="", limit=15)
        except Exception as exc:
            logger.info(f"[Scheduler] 拉取早报通知失败 ({tenant_id}): {exc}")
            news_items = []
        if news_items:
            keys = _split_keywords(news_keywords) or _student_major_keywords(tenant_id, user_id)
            matched = [
                n for n in news_items
                if any(k in (n.title + " " + n.site).lower() for k in keys)
            ] if keys else news_items
            picked = (matched or news_items)[:3]
            tag = f"（匹配关键词：{' / '.join(keys[:3])}）" if matched and keys else ""
            lines.append(f"\n**最新通知速览{tag}**")
            for n in picked:
                date_part = f" [{n.date}]" if n.date else ""
                lines.append(f"- [{n.title}]({n.url}){date_part}")

    # 3. 伴学闪卡复习提醒
    due_cards = _due_flashcard_count(tenant_id, user_id)
    if due_cards > 0:
        lines.append(f"\n**伴学复习**：你有 **{due_cards}** 张课程考点闪卡已到复习时间，回网页端「伴学空间」刷几分钟巩固一下～")

    return "\n".join(lines)


def _grade_key(item: dict[str, Any]) -> str:
    return f"{item.get('term', '')}|{item.get('course', '')}|{item.get('credit', '')}"


def compute_grades_hash(grades: list[dict[str, Any]]) -> str:
    """对成绩列表做顺序无关的稳定哈希。"""
    normalized = sorted(
        (
            str(g.get("term", "")).strip(),
            str(g.get("course", "")).strip(),
            str(g.get("score", "")).strip(),
            str(g.get("credit", "")).strip(),
            str(g.get("gpa", "")).strip(),
        )
        for g in grades
        if str(g.get("score", "")).strip()
    )
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _grades_snapshot_key(tenant_id: str, user_id: str) -> str:
    return f"im:grades_map:{tenant_id}:{user_id}"


def _read_prev_grade_map(tenant_id: str, user_id: str) -> dict[str, str]:
    key = _grades_snapshot_key(tenant_id, user_id)
    try:
        raw = _redis().get(key)
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return dict(_LOCAL_GRADES.get(key, {}))


def _write_prev_grade_map(tenant_id: str, user_id: str, grade_map: dict[str, str]) -> None:
    key = _grades_snapshot_key(tenant_id, user_id)
    try:
        _redis().set(key, json.dumps(grade_map, ensure_ascii=False), ex=180 * 86400)
    except Exception:
        pass
    _LOCAL_GRADES[key] = dict(grade_map)


def _credit_weighted_gpa(grades: list[dict[str, Any]]) -> float | None:
    total_w = 0.0
    total_c = 0.0
    for g in grades:
        try:
            gpa = float(g.get("gpa") or "")
            credit = float(g.get("credit") or "")
        except ValueError:
            continue
        if credit > 0:
            total_w += gpa * credit
            total_c += credit
    return round(total_w / total_c, 2) if total_c > 0 else None


def check_and_build_grade_alert(
    binding: im_gateway.ImBinding,
    grades: list[dict[str, Any]],
) -> str | None:
    """比对本次成绩与历史快照；首次建立基线不推打扰，有新增或变动时返回推送文案。"""
    current_map = {
        _grade_key(g): str(g.get("score", "")).strip()
        for g in grades
        if str(g.get("course", "")).strip() and str(g.get("score", "")).strip()
    }
    if not current_map:
        return None
    new_hash = compute_grades_hash(grades)
    if not binding.last_grade_hash:
        im_gateway.update_grade_hash(binding.id, new_hash)
        _write_prev_grade_map(binding.tenant_id, binding.user_id, current_map)
        return None
    if binding.last_grade_hash == new_hash:
        return None

    prev_map = _read_prev_grade_map(binding.tenant_id, binding.user_id)
    changed: list[dict[str, Any]] = []
    for g in grades:
        k = _grade_key(g)
        score = str(g.get("score", "")).strip()
        if score and prev_map.get(k) != score:
            changed.append(g)

    im_gateway.update_grade_hash(binding.id, new_hash)
    _write_prev_grade_map(binding.tenant_id, binding.user_id, current_map)
    if not changed:
        return None

    lines = ["### 🎓 出分提醒：有新成绩发布！"]
    for g in changed[:8]:
        gpa_part = f"（绩点 {g['gpa']}）" if g.get("gpa") else ""
        credit_part = f" · {g['credit']} 学分" if g.get("credit") else ""
        lines.append(f"- **{g.get('course')}**：**{g.get('score')}**{gpa_part}{credit_part}")
    overall_gpa = _credit_weighted_gpa(grades)
    if overall_gpa is not None:
        lines.append(f"\n当前统计学分加权 GPA：**{overall_gpa}**")
    return "\n".join(lines)


def notify_ticket_reply(ticket: Any) -> int:
    """工单回复或回流后，推送通知给学生已开启 ticketAlert 的绑定端。"""
    bindings = im_gateway.list_user_bindings(ticket.tenant_id, ticket.user_id)
    sent = 0
    for b in bindings:
        if not b.get("ticket_alert"):
            continue
        text = (
            f"### 📩 你的人工求助工单已得到回复\n"
            f"- 问题：{ticket.question[:80]}\n"
            f"- 老师回复：{ticket.reply}\n"
            + ("- 该解答已回流学校知识库，方便后续同学查询。" if ticket.status == "merged" else "")
        ).strip()
        if im_gateway.send_im_message(ticket.tenant_id, str(b["platform"]), str(b["external_id"]), text):
            sent += 1
    return sent


def _within_digest_window(now: datetime, hhmm: str) -> bool:
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except Exception:
        h, m = 7, 30
    target_min = h * 60 + m
    now_min = now.hour * 60 + now.minute
    return abs(now_min - target_min) <= DIGEST_WINDOW_MINUTES


def run_once(now: datetime | None = None, *, force_digest: bool = False) -> dict[str, int]:
    """执行一轮主动推送检查（早报 + 出分提醒），可在 CLI/测试/后台协程复用。"""
    current = now or datetime.now()
    today = current.date()
    digests_sent = 0
    grade_alerts_sent = 0

    bindings = im_gateway.list_active_bindings()
    # 同一个 (tenant, user) 共享一份早报正文与成绩快照，减少对教务的重复请求
    digest_cache: dict[tuple[str, str, str], str] = {}
    grade_checked_users: set[tuple[str, str]] = set()
    grades_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for b in bindings:
        if b.morning_digest and (force_digest or _within_digest_window(current, b.digest_time)):
            dedupe_key = f"im:sent:digest:{b.id}:{today.isoformat()}"
            if force_digest or _acquire_once(dedupe_key, 86400):
                cache_key = (b.tenant_id, b.user_id, b.news_keywords)
                digest_text = digest_cache.get(cache_key)
                if digest_text is None:
                    digest_text = build_morning_digest(b.tenant_id, b.user_id, today, b.news_keywords)
                    digest_cache[cache_key] = digest_text
                if im_gateway.send_im_message(b.tenant_id, b.platform, b.external_id, digest_text):
                    digests_sent += 1

        if b.grade_alert:
            u_key = (b.tenant_id, b.user_id)
            if u_key not in grade_checked_users:
                grade_checked_users.add(u_key)
                if _acquire_once(f"im:chk:grade:{b.tenant_id}:{b.user_id}", GRADE_CHECK_INTERVAL_SECONDS):
                    try:
                        mode = school_ai(b.tenant_id).jw_mode
                        if mode in ("demo", "zhengfang"):
                            xnm, xqm = current_term(today)
                            with jw_account.source_for(b.tenant_id, b.user_id) as jw:
                                grades_cache[u_key] = jw.grades(xnm, xqm)
                    except (JwAuthError, JwError, redis.RedisError):
                        pass
            if u_key in grades_cache:
                alert = check_and_build_grade_alert(b, grades_cache[u_key])
                if alert and im_gateway.send_im_message(b.tenant_id, b.platform, b.external_id, alert):
                    grade_alerts_sent += 1

    return {"digests_sent": digests_sent, "grade_alerts_sent": grade_alerts_sent}


class ImScheduler:
    """FastAPI 后台定时推送调度器。默认需设置 IM_SCHEDULER_ENABLED=true 启用。"""

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> bool:
        if os.getenv("IM_SCHEDULER_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
            return False
        if self._task is not None:
            return True
        self._task = asyncio.create_task(self._loop(), name="im-scheduler")
        return True

    async def _loop(self) -> None:
        logger.info("[Scheduler] 主动早报与出分提醒后台调度已启动")
        while not self._stop.is_set():
            try:
                stats = await asyncio.to_thread(run_once)
                if stats["digests_sent"] or stats["grade_alerts_sent"]:
                    logger.info(f"[Scheduler] 本轮推送完成: {stats}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"[Scheduler] 本轮调度异常: {exc}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=SCHEDULER_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


scheduler = ImScheduler()
