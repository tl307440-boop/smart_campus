"""工具型问答：模型按需调用教务、学校通知、联网搜索、地图、知识库，再据结果作答。

每校能用哪些工具看「AI 服务」页的配置：没填地图 Key 就没有地图工具，教务关着就没有教务工具。
嵌入挂件的访客没有账号，拿不到教务工具。「只按资料回答」的学校不开放联网搜索。
每次调用都记一条审计（谁、调了什么、成没成、耗时），不记返回内容。
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import jw_account
import redis
import school_tools
from ai_config import ModelEndpoint, client_for, map_key, search_key
from jw_zhengfang import JwAuthError, JwError, current_term
from logging_config import get_logger
from workspace import WorkspaceSettings, school_ai, school_name

logger = get_logger(__name__)

MAX_ROUNDS = 4
TOOL_RESULT_CHARS = 6000
AUDIT_KEEP = 2000
WEEKDAYS = "一二三四五六日"

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
    return _client


@dataclass
class ToolContext:
    tenant_id: str
    user_id: str
    settings: WorkspaceSettings
    search_knowledge: Callable[[str], tuple[str, list[dict[str, Any]]]]
    extras: str = ""  # 学校人设 + 用户记忆，见 llm_service.prompt_extras
    refusal: str = ""  # 「只按资料回答」的学校：工具没查到时原样回复这句
    today: date = field(default_factory=date.today)
    sources: list[dict[str, Any]] = field(default_factory=list)
    _campus_id: str = ""

    def cite(self, title: str, url: str = "", snippet: str = "") -> None:
        if any(s.get("url") == url and url for s in self.sources):
            return
        self.sources.append({
            "index": len(self.sources) + 1, "source": title, "url": url,
            "snippet": snippet[:240], "score": None, "rerank_score": None,
        })


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[ToolContext, dict[str, Any]], Any]

    def spec(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def _obj(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties or {}, "required": required or []}


_DAY = {"type": "string", "enum": ["today", "tomorrow", "this_week", "next_week"], "description": "today 今天 / tomorrow 明天 / this_week 本周 / next_week 下周"}
_WEEKDAY = {"type": "integer", "minimum": 1, "maximum": 7, "description": "指定星期几（1=周一，7=周日），给了就只看这一天"}
_TERM = {"type": "string", "enum": ["current", "previous", "all"], "description": "current 本学期 / previous 上学期 / all 全部"}


# ---------------- 教务 ----------------


def _weeks_of(zcd: str) -> set[int]:
    """「1-8周,10-16周(双)」→ 周次集合。"""
    weeks: set[int] = set()
    for part in re.split(r"[,，]", zcd):
        match = re.search(r"(\d+)(?:-(\d+))?周?(?:\((单|双)\))?", part)
        if not match:
            continue
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        parity = {"单": 1, "双": 0}.get(match.group(3) or "")
        weeks |= {w for w in range(start, end + 1) if parity is None or w % 2 == parity}
    return weeks


def _periods_of(jcs: str) -> list[int]:
    match = re.search(r"(\d+)(?:-(\d+))?", jcs)
    if not match:
        return []
    return list(range(int(match.group(1)), int(match.group(2) or match.group(1)) + 1))


def _previous_term(xnm: str, xqm: str) -> tuple[str, str]:
    return (str(int(xnm) - 1), "12") if xqm == "3" else (xnm, "3")


def _term(ctx: ToolContext, which: str) -> tuple[str, str]:
    current = current_term(ctx.today)
    if which == "previous":
        return _previous_term(*current)
    if which == "all":
        return "", ""
    return current


def _int_arg(value: Any, low: int, high: int) -> int | None:
    """模型传来的数字参数：转成整数并限定范围，不合法就当没给。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def _periods_arg(value: Any) -> list[int]:
    """节次参数：[1,2]、["1-2"]、"3-4" 都认。"""
    items = value if isinstance(value, list) else [value]
    periods: set[int] = set()
    for item in items:
        number = _int_arg(item, 1, 14)
        if number is not None:
            periods.add(number)
        elif isinstance(item, str):
            periods.update(p for p in _periods_of(item) if 1 <= p <= 14)
    return sorted(periods)


def _target_days(ctx: ToolContext, day: str, weekday: Any) -> list[tuple[date, int]]:
    """要看的日期，以及每天相对今天差几周。"""
    weekday = _int_arg(weekday, 1, 7)
    today = ctx.today
    monday = today - timedelta(days=today.weekday())
    if day == "tomorrow":
        days = [today + timedelta(days=1)]
    elif day in ("this_week", "next_week"):
        start = monday + timedelta(days=7 if day == "next_week" else 0)
        days = [start + timedelta(days=i) for i in range(7)]
        if weekday:
            days = [days[weekday - 1]]
    elif weekday:
        days = [monday + timedelta(days=weekday - 1)]
        if days[0] < today:
            days = [days[0] + timedelta(days=7)]
    else:
        days = [today]
    return [(d, (d - timedelta(days=d.weekday()) - monday).days // 7) for d in days]


def _timetable(ctx: ToolContext, args: dict[str, Any]) -> Any:
    xnm, xqm = current_term(ctx.today)
    with jw_account.source_for(ctx.tenant_id, ctx.user_id) as jw:
        week = jw.current_week()
        data = jw.timetable(xnm, xqm)
    ctx._campus_id = data.get("campus_id", "")
    times = {p["period"]: p for p in data.get("periods", [])}
    out = []
    for day, week_shift in _target_days(ctx, str(args.get("day") or "today"), args.get("weekday")):
        target_week = (week or 0) + week_shift
        lessons = []
        for c in sorted(data["courses"], key=lambda c: _periods_of(c["periods"])[:1] or [0]):
            if c["weekday"] != day.isoweekday() or (week and target_week not in _weeks_of(c["weeks"])):
                continue
            periods = _periods_of(c["periods"])
            start = times.get(str(periods[0]), {}).get("start", "") if periods else ""
            end = times.get(str(periods[-1]), {}).get("end", "") if periods else ""
            lessons.append({**c, "time": f"{start}-{end}" if start else ""})
        out.append({"date": day.isoformat(), "weekday": f"星期{WEEKDAYS[day.weekday()]}", "week": target_week or None, "courses": lessons})
    ctx.cite("教务系统·个人课表")
    result: dict[str, Any] = {"current_week": week, "days": out}
    if not week:
        result["week_unknown"] = "教务没给出当前是第几周（可能在假期或新学期还没开始），上面列的是这个星期几所有的课，没按周次筛，请提醒学生以教务系统为准"
    return result


def _exams(ctx: ToolContext, args: dict[str, Any]) -> Any:
    xnm, xqm = _term(ctx, str(args.get("term") or "current"))
    with jw_account.source_for(ctx.tenant_id, ctx.user_id) as jw:
        exams = jw.exams(xnm or current_term(ctx.today)[0], xqm or current_term(ctx.today)[1])
    ctx.cite("教务系统·考试安排")
    return {"term": f"{xnm}-{xqm}", "exams": exams, "note": "" if exams else "这个学期还没有排考试，或考试安排未发布"}


def _grades(ctx: ToolContext, args: dict[str, Any]) -> Any:
    xnm, xqm = _term(ctx, str(args.get("term") or "all"))
    with jw_account.source_for(ctx.tenant_id, ctx.user_id) as jw:
        grades = jw.grades(xnm, xqm)
    credits = points = 0.0
    for g in grades:
        try:
            credit, gpa = float(g["credit"]), float(g["gpa"])
        except (TypeError, ValueError):
            continue
        credits += credit
        points += credit * gpa
    ctx.cite("教务系统·成绩")
    return {
        "count": len(grades),
        "credit_weighted_gpa": round(points / credits, 2) if credits else None,
        "grades": grades,
    }


def _free_rooms(ctx: ToolContext, args: dict[str, Any]) -> Any:
    xnm, xqm = current_term(ctx.today)
    periods = _periods_arg(args.get("periods") or []) or [1, 2]
    (day, week_shift), *_ = _target_days(ctx, str(args.get("day") or "today"), args.get("weekday"))
    building = str(args.get("building") or "").strip()
    with jw_account.source_for(ctx.tenant_id, ctx.user_id) as jw:
        week = jw.current_week() or 1
        campus = ctx._campus_id or jw.timetable(xnm, xqm).get("campus_id", "") or "1"
        rooms = jw.free_rooms(xnm, xqm, campus, week + week_shift, day.isoweekday(), periods)
    if building:
        rooms = [r for r in rooms if building in r["room"]] or rooms
    ctx.cite("教务系统·空教室")
    return {
        "date": day.isoformat(), "weekday": f"星期{WEEKDAYS[day.weekday()]}", "periods": periods,
        "count": len(rooms), "rooms": rooms[:40],
    }


# ---------------- 学校通知、搜索、地图、知识库 ----------------


def _news(ctx: ToolContext, args: dict[str, Any]) -> Any:
    items = school_tools.school_news(school_ai(ctx.tenant_id).news_urls, str(args.get("keyword") or ""))
    for item in items[:5]:
        ctx.cite(item["title"], item["url"], item["date"])
    return {"items": items, "note": "" if items else "官网通知里没有标题含这些词的"}


def _read_notice(ctx: ToolContext, args: dict[str, Any]) -> Any:
    notice = school_tools.read_notice(str(args.get("url") or ""), school_ai(ctx.tenant_id).news_urls)
    ctx.cite(notice["title"], notice["url"], notice["text"])
    return notice


def _web_search(ctx: ToolContext, args: dict[str, Any]) -> Any:
    key = search_key(ctx.tenant_id)
    results = school_tools.web_search(key.key if key else "", str(args.get("query") or ""))
    for r in results[:4]:
        ctx.cite(r["title"], r["url"], r["summary"])
    return {"results": results}


def _place(ctx: ToolContext, args: dict[str, Any]) -> Any:
    name = str(args.get("name") or "").strip()
    school = school_name(ctx.tenant_id)
    key = map_key(ctx.tenant_id).key  # type: ignore[union-attr]
    places = school_tools.find_places(key, school, name)
    for p in places[:3]:
        ctx.cite(f"高德地图·{p.name}", p.link, p.address)
    result: dict[str, Any] = {
        "places": [{"name": p.name, "address": p.address, "map_link": p.link} for p in places],
        "search_link": school_tools.search_link(f"{school} {name}".strip()),
    }
    if not places:
        # 地图上常常只标了学校本身，没标校内的楼和门：至少能带新生先到学校
        campus = school_tools.campus(key, school)
        if campus:
            ctx.cite(f"高德地图·{campus.name}", campus.link, campus.address)
            result["campus"] = {"name": campus.name, "address": campus.address, "map_link": campus.link}
        result["note"] = f"地图上没有标注「{name}」，校内的具体位置以学校资料（search_knowledge）为准，可以先导航到学校"
    return result


def _route(ctx: ToolContext, args: dict[str, Any]) -> Any:
    route = school_tools.walking_route(
        map_key(ctx.tenant_id).key, school_name(ctx.tenant_id),  # type: ignore[union-attr]
        str(args.get("origin") or ""), str(args.get("destination") or ""),
    )
    ctx.cite(f"高德地图·{route['from']} → {route['to']}", route["nav_link"], f"步行约 {route['minutes']} 分钟")
    return route


def _knowledge(ctx: ToolContext, args: dict[str, Any]) -> Any:
    context, sources = ctx.search_knowledge(str(args.get("query") or ""))
    for s in sources:
        ctx.cite(s.get("source", "本校资料"), "", s.get("snippet", ""))
    return {"context": context or "本校资料里没有找到相关内容"}


_JW_TOOLS = [
    Tool("jw_timetable", "查课表", "查询当前学生本人的课表（课程、时间、教室、老师）。", _obj({"day": _DAY, "weekday": _WEEKDAY}), _timetable),
    Tool("jw_exams", "查考试安排", "查询当前学生本人的考试安排（时间、考场、座位号）。", _obj({"term": {**_TERM, "enum": ["current", "previous"]}}), _exams),
    Tool("jw_grades", "查成绩", "查询当前学生本人的成绩、学分和绩点。", _obj({"term": _TERM}), _grades),
    Tool(
        "jw_free_rooms", "查空教室", "查询某天某几节课没被占用的教室，可按楼名过滤。",
        _obj({"day": {**_DAY, "enum": ["today", "tomorrow", "this_week"]}, "weekday": _WEEKDAY,
              "periods": {"type": "array", "items": {"type": "integer"}, "description": "第几节课，如 [1,2] 表示 1-2 节"},
              "building": {"type": "string", "description": "教学楼名称里的关键词，可不填"}}),
        _free_rooms,
    ),
]
_NEWS_TOOLS = [
    Tool("school_news", "查学校通知", "查学校官网和教务处最新的通知、公告、新闻标题，可按关键词筛选。", _obj({"keyword": {"type": "string", "description": "标题关键词，多个用空格分开，可不填"}}), _news),
    Tool("read_notice", "读通知正文", "打开 school_news 返回的某条通知，读正文。", _obj({"url": {"type": "string"}}, ["url"]), _read_notice),
]
_SEARCH_TOOL = Tool("web_search", "联网搜索", "联网搜索最新信息。校内的事先用 school_news 或 search_knowledge。", _obj({"query": {"type": "string"}}, ["query"]), _web_search)
_MAP_TOOLS = [
    Tool("campus_place", "找地点", "在地图上找学校里或学校附近的地点（教学楼、食堂、校门、医院、地铁站等），返回地址和导航链接。", _obj({"name": {"type": "string", "description": "地点名，如 图书馆、北门"}}, ["name"]), _place),
    Tool("walking_route", "规划步行路线", "规划学校里或附近两个地点之间的步行路线。", _obj({"origin": {"type": "string"}, "destination": {"type": "string"}}, ["origin", "destination"]), _route),
]
_KNOWLEDGE_TOOL = Tool("search_knowledge", "查本校资料", "检索学校上传的资料（学生手册、规章、校园指南、地点介绍等）。", _obj({"query": {"type": "string"}}, ["query"]), _knowledge)


def available_tools(tenant_id: str, user_id: str, settings: WorkspaceSettings) -> list[Tool]:
    school = school_ai(tenant_id)
    tools = [_KNOWLEDGE_TOOL]
    if user_id and school.jw_mode in ("demo", "zhengfang"):
        tools += _JW_TOOLS
    if school.news_urls:
        tools += _NEWS_TOOLS
    if map_key(tenant_id):
        tools += _MAP_TOOLS
    if search_key(tenant_id) and not settings.query_only:
        tools.append(_SEARCH_TOOL)
    return tools


def has_live_tools(tenant_id: str, user_id: str, settings: WorkspaceSettings) -> bool:
    """除了知识库还有别的工具，意图分类才值得多一个 TOOL 选项。"""
    return len(available_tools(tenant_id, user_id, settings)) > 1


# ---------------- 执行与审计 ----------------


def _audit(ctx: ToolContext, tool: str, args: dict[str, Any], ok: bool, error: str, ms: int) -> None:
    # 只记参数里的关键词，不记返回的个人数据
    entry = {"ts": int(time.time()), "user": ctx.user_id or "guest", "tool": tool,
             "args": {k: str(v)[:60] for k, v in args.items()}, "ok": ok, "error": error[:120], "ms": ms}
    logger.info(f"[Agent] 工具 {tool} {'成功' if ok else '失败'} {ms}ms", extra={"tenant_id": ctx.tenant_id})
    try:
        key = f"tool_audit:{ctx.tenant_id}"
        _redis().lpush(key, json.dumps(entry, ensure_ascii=False))
        _redis().ltrim(key, 0, AUDIT_KEEP - 1)
    except redis.RedisError as exc:
        logger.warning(f"[Agent] 审计写入失败: {exc}")


def run_tool(ctx: ToolContext, tool: Tool, raw_args: str) -> tuple[str, bool]:
    """返回 (交给模型的 JSON 文本, 是否成功)。"""
    started = time.monotonic()
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        args = {}
    try:
        result: Any = tool.run(ctx, args)
        ok, error = True, ""
    except JwAuthError as exc:
        result, ok, error = {"error": str(exc), "how_to_fix": "请学生打开「工具 → 教务账号」绑定（或重新绑定）统一身份认证账号"}, False, str(exc)
    except (JwError, school_tools.ToolError) as exc:
        result, ok, error = {"error": str(exc)}, False, str(exc)
    except Exception as exc:  # 工具出错不能拖垮整轮：告诉模型这一步没查到
        logger.exception(f"[Agent] 工具 {tool.name} 异常")
        result, ok, error = {"error": "这项查询暂时不可用"}, False, type(exc).__name__
    _audit(ctx, tool.name, args, ok, error, int((time.monotonic() - started) * 1000))
    text = json.dumps(result, ensure_ascii=False, default=str)
    return (text if len(text) <= TOOL_RESULT_CHARS else text[:TOOL_RESULT_CHARS] + "…（已截断）"), ok


# ---------------- 对话循环 ----------------


def system_prompt(ctx: ToolContext, tools: list[Tool]) -> str:
    school = school_name(ctx.tenant_id) or "本校"
    names = {t.name for t in tools}
    missing = []
    if "jw_timetable" not in names:
        missing.append("个人课表/成绩/考试（" + ("访客不能查" if not ctx.user_id else "学校还没开通教务查询") + "）")
    if "campus_place" not in names:
        missing.append("地图导航（学校还没配地图 Key）")
    gaps = f"\n目前查不了：{'；'.join(missing)}。被问到时如实说明，并建议去问辅导员或相关部门。" if missing else ""
    if ctx.refusal:
        gaps += f"\n- 本校要求只依据资料回答：答案必须来自工具结果，不能用你自己的知识补充；工具都没查到相关内容时，只回复「{ctx.refusal}」；但工具返回 error 或 how_to_fix 时，照常把那条提示转述给学生。"
    return f"""你是"{ctx.settings.assistant_name}"，{school}的智能校园助手，像热心的学长学姐，对刚入学、不认路的新生特别耐心。
今天是 {ctx.today.isoformat()}（星期{WEEKDAYS[ctx.today.weekday()]}）。

你可以调用工具。规则：
- 涉及具体事实（课程、时间、教室、成绩、通知内容、地点）必须以工具结果为准，不要编造；查不到就直说。
- 问本人课表、考试、成绩、空教室：调用教务工具。工具提示没绑定或过期时，告诉学生去「工具 → 教务账号」绑定。
- 问学校的通知、安排、活动：先用 school_news 找，需要细节再 read_notice 读正文。
- 问校园里的地方在哪、怎么走：先 search_knowledge 看学校资料，再用地图工具，给出导航链接。
- 回答用简体中文 Markdown，先给结论，再分点列细节；链接用 [文字](网址) 的形式。
- 成绩等个人信息只回答本人问到的部分。{gaps}
""" + ctx.extras


@dataclass
class _Turn:
    content: str = ""
    calls: list[dict[str, str]] = field(default_factory=list)


def _complete(endpoint: ModelEndpoint, messages: list[dict[str, Any]], specs: list[dict[str, Any]], final: bool, emit: Callable[[str], None]) -> _Turn:
    client = client_for(endpoint)
    kwargs: dict[str, Any] = {"model": endpoint.model, "messages": messages, "temperature": 0.3, "tools": specs}
    if final:
        kwargs["tool_choice"] = "none"
    turn = _Turn()
    if not endpoint.stream:
        message = client.chat.completions.create(**kwargs).choices[0].message
        turn.content = message.content or ""
        emit(turn.content)
        turn.calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments or ""} for c in (message.tool_calls or [])]
        return turn
    calls: dict[int, dict[str, str]] = {}
    for chunk in client.chat.completions.create(stream=True, **kwargs):
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            turn.content += delta.content
            emit(delta.content)
        for part in delta.tool_calls or []:
            slot = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
            slot["id"] = part.id or slot["id"]
            if part.function:
                slot["name"] += part.function.name or ""
                slot["arguments"] += part.function.arguments or ""
    turn.calls = [calls[i] for i in sorted(calls)]
    return turn


def run(
    ctx: ToolContext,
    endpoint: ModelEndpoint,
    history: list[dict[str, Any]],
    question: str,
    emit: Callable[[str], None],
    on_tool: Callable[[dict[str, Any]], None],
) -> str:
    """跑完整个工具循环，返回最终回答全文（中途已经边生成边 emit 出去）。"""
    tools = available_tools(ctx.tenant_id, ctx.user_id, ctx.settings)
    by_name = {t.name: t for t in tools}
    specs = [t.spec() for t in tools]
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt(ctx, tools)}, *history, {"role": "user", "content": question}]
    parts: list[str] = []

    def say(text: str) -> None:
        if text:
            parts.append(text)
            emit(text)

    for round_no in range(MAX_ROUNDS + 1):
        turn = _complete(endpoint, messages, specs, round_no == MAX_ROUNDS, say)
        if not turn.calls:
            break
        if turn.content and not turn.content.endswith("\n"):
            say("\n\n")
        messages.append({
            "role": "assistant", "content": turn.content or None,
            "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in turn.calls],
        })
        for call in turn.calls:
            tool = by_name.get(call["name"])
            label = tool.label if tool else call["name"]
            on_tool({"name": call["name"], "label": label, "state": "running"})
            if tool:
                result, ok = run_tool(ctx, tool, call["arguments"])
            else:
                result, ok = json.dumps({"error": "没有这个工具"}, ensure_ascii=False), False
            on_tool({"name": call["name"], "label": label, "state": "done", "ok": ok})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    return "".join(parts)
