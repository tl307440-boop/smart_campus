"""管理员「使用情况」页的统计：只做纯计算，不连库、不连 Redis，见 test_usage_stats.py。

输入是 usage_log 取出来的提问记录和 agent 的工具审计，输出直接给前端画。
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# 提问记录的结果。comfort 是情绪倾诉：不存原文，只计数
ANSWERED = "answered"
REFUSED = "refused"
NO_SOURCE = "no_source"
TOOL = "tool"
COMFORT = "comfort"

QUESTION_MAX_CHARS = 300
TOP_QUESTIONS = 10
UNANSWERED_LIMIT = 20
SAME_QUESTION = 0.5

_FILLER = re.compile(r"请问|麻烦|帮我|帮忙|我想问一下|我想问|想问一下|问一下|一下|是不是|能不能|可不可以|可以|请|吗|呢|呀|啊|吧|嘛")
_NOT_TEXT = re.compile(r"[\W_]+")


def classify(intent: str, answer: str, sources: list[Any], tool_names: list[str], refusal: str) -> str:
    """一轮对话的结局。判得保守：不确定就算答上了，宁可少报缺口，也别把闲聊算成没答上来。"""
    if intent in ("CRISIS", "COMFORT"):
        return COMFORT
    if refusal and answer.strip() == refusal.strip():
        return REFUSED
    if tool_names or intent == "TOOL":
        return TOOL
    if intent == "RAG" and not sources:
        return NO_SOURCE
    return ANSWERED


def _normalize(text: str) -> str:
    return _NOT_TEXT.sub("", _FILLER.sub("", text.lower()))


def _bigrams(text: str) -> frozenset[str]:
    if len(text) < 2:
        return frozenset([text]) if text else frozenset()
    return frozenset(text[i : i + 2] for i in range(len(text) - 1))


def _similar(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    shared = len(a & b)
    if shared / len(a | b) >= SAME_QUESTION:
        return True
    # 一句话几乎完整地包含在另一句里：「补办学生证」和「学生证丢了怎么补办学生证」
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return len(small) >= 3 and shared / len(small) >= 0.85


@dataclass
class Group:
    key: frozenset[str]
    variants: Counter[str] = field(default_factory=Counter)
    count: int = 0
    last_at: datetime | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        """同一类问题里最常见的那种问法；一样多取短的。"""
        return min(self.variants, key=lambda item: (-self.variants[item], len(item)))


def cluster(rows: Iterable[dict[str, Any]]) -> list[Group]:
    """把说法不同的同一个问题归成一组。按数量从多到少，数量相同的新近的在前。"""
    groups: list[Group] = []
    for row in rows:
        text = " ".join(str(row.get("question") or "").split())
        key = _bigrams(_normalize(text))
        if not key:
            continue
        match = next((group for group in groups if _similar(key, group.key)), None)
        if match is None:
            match = Group(key=key)
            groups.append(match)
        match.variants[text] += 1
        match.count += 1
        match.rows.append(row)
        at = row.get("at")
        if isinstance(at, datetime) and (match.last_at is None or at > match.last_at):
            match.last_at = at
    groups.sort(key=lambda group: (-group.count, -(group.last_at.timestamp() if group.last_at else 0.0)))
    return groups


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _unanswered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps = [row for row in rows if row["outcome"] in (REFUSED, NO_SOURCE) or row.get("rating") == -1]
    found = []
    for group in cluster(gaps)[:UNANSWERED_LIMIT]:
        kinds = Counter()
        reasons = Counter()
        hint = ""
        for row in sorted(group.rows, key=lambda r: r.get("at") or datetime.min, reverse=True):
            if row["outcome"] == REFUSED:
                kinds["refused"] += 1
            elif row["outcome"] == NO_SOURCE:
                kinds["no_source"] += 1
            if row.get("rating") == -1:
                kinds["downvoted"] += 1
                if row.get("reason"):
                    reasons[row["reason"]] += 1
                hint = hint or str(row.get("answer") or "")[:200]
        found.append({
            "question": group.text,
            "count": group.count,
            "last_at": _iso(group.last_at),
            "kinds": dict(kinds),
            "reasons": dict(reasons),
            "answer_hint": hint,
        })
    return found


def _tools(audits: Iterable[dict[str, Any]], labels: dict[str, str], since: float) -> list[dict[str, Any]]:
    calls: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    total_ms: Counter[str] = Counter()
    last_error: dict[str, tuple[float, str]] = {}
    for item in audits:
        ts, name = item.get("ts"), str(item.get("tool") or "")
        if not name or not isinstance(ts, (int, float)) or ts < since:
            continue
        calls[name] += 1
        total_ms[name] += int(item.get("ms") or 0)
        if item.get("ok") is False:
            failures[name] += 1
            error = str(item.get("error") or "")
            if error and ts >= last_error.get(name, (0.0, ""))[0]:
                last_error[name] = (float(ts), error)
    return [
        {
            "name": name,
            "label": labels.get(name, name),
            "calls": count,
            "failures": failures[name],
            "failure_rate": round(failures[name] / count, 3),
            "avg_ms": round(total_ms[name] / count),
            "last_error": last_error.get(name, (0.0, ""))[1],
        }
        for name, count in calls.most_common()
    ]


def build_report(
    rows: list[dict[str, Any]],
    audits: Iterable[dict[str, Any]],
    labels: dict[str, str],
    days: int,
    now: float,
) -> dict[str, Any]:
    """rows 每行：question, outcome, guest, at, rating(None/1/-1), reason, answer。"""
    outcomes = Counter(row["outcome"] for row in rows)
    asked = [row for row in rows if row["outcome"] != COMFORT and row.get("question")]
    judged = len(rows) - outcomes[COMFORT]
    good = outcomes[ANSWERED] + outcomes[TOOL]
    tools = _tools(audits, labels, now - days * 86400)

    top = [
        {"question": group.text, "count": group.count, "last_at": _iso(group.last_at)}
        for group in cluster(asked)[:TOP_QUESTIONS]
    ]
    return {
        "days": days,
        "totals": {
            "questions": len(rows),
            "guest": sum(1 for row in rows if row.get("guest")),
            "comfort": outcomes[COMFORT],
            "refused": outcomes[REFUSED],
            "no_source": outcomes[NO_SOURCE],
            "answer_rate": round(good / judged, 3) if judged else None,
            "up": sum(1 for row in rows if row.get("rating") == 1),
            "down": sum(1 for row in rows if row.get("rating") == -1),
            "tool_calls": sum(tool["calls"] for tool in tools),
            "tool_failures": sum(tool["failures"] for tool in tools),
        },
        "top_questions": top,
        "unanswered": _unanswered(asked),
        "tools": tools,
    }
