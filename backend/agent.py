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

import httpx
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
# 空教室交给模型的行数；完整结果走表格，不受这个限制
ROOMS_FOR_MODEL = 40
# 一张表最多带多少行给前端，防止一次查询把消息撑到几百 KB
MAX_FOLLOW_UPS = 3
TABLE_MAX_ROWS = 300
TABLE_SHOWN = "完整结果已经以表格形式显示在你的回答下方，不要再逐行列出，用一两句话总结重点即可"

# 命中这些词就直接走工具问答，不交给意图分类模型去猜（见 needs_tools）
_JW_WORDS = (
    "成绩", "绩点", "学分", "挂科", "补考", "重修", "课表", "有课", "什么课", "几节课", "第几节",
    "考试", "考场", "座位号", "空教室", "空闲教室", "自习室", "教室空", "毕业", "学业",
    "第几周", "第几个周", "周次", "教学周", "校历", "开学", "放假",
)
_NEWS_WORDS = ("通知", "公告", "新闻", "讲座", "活动安排")
_TIMELY_WORDS = (
    "今年", "明年", "去年", "最新", "现在", "目前", "最近", "几号", "还有多久", "多少钱", "价格", "排名", "排行", "天气",
)
_YEAR = re.compile(r"20\d{2}\s*年?")

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
    # 工具查出的多行结果：原样交给前端画成表格，模型碰不到行数据
    tables: list[dict[str, Any]] = field(default_factory=list)
    # 查完之后值得顺着问的下一句，前端画成点一下就问的小按钮
    follow_ups: list[str] = field(default_factory=list)
    _campus_id: str = ""

    def suggest(self, *questions: str) -> None:
        for question in questions:
            if question and question not in self.follow_ups and len(self.follow_ups) < MAX_FOLLOW_UPS:
                self.follow_ups.append(question)

    def cite(self, title: str, url: str = "", snippet: str = "") -> None:
        if any(s.get("url") == url and url for s in self.sources):
            return
        self.sources.append({
            "index": len(self.sources) + 1, "source": title, "url": url,
            "snippet": snippet[:240], "score": None, "rerank_score": None,
        })

    def table(self, title: str, columns: list[tuple[str, str]], rows: list[dict[str, Any]]) -> str:
        """登记一张表，返回给模型的提示语。columns 是 (字段名, 表头) 的列表；整列都空的不要。"""
        cells = [{key: "" if row.get(key) is None else str(row.get(key)) for key, _ in columns} for row in rows[:TABLE_MAX_ROWS]]
        kept = [(key, label) for key, label in columns if any(cell[key] for cell in cells)]
        self.tables.append({
            "title": title,
            "columns": [{"key": key, "label": label} for key, label in kept],
            "rows": [{key: cell[key] for key, _ in kept} for cell in cells],
            "total": len(rows),
        })
        return TABLE_SHOWN


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


weeks_of = _weeks_of
periods_of = _periods_of


def first_monday(today: date, week: int) -> date:
    """今天是教学第 week 周时，第 1 周的周一是哪天。"""
    return today - timedelta(days=today.weekday()) - timedelta(weeks=week - 1)


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


def lessons_on(data: dict[str, Any], day: date, week: int | None) -> list[dict[str, Any]]:
    """课表里落在 day 这一天的课，按节次排好；week 是 day 所在的第几周，不知道就别按周次筛。

    每节课多带 time（"08:00-09:40"）、start、end，教务没给节次时间时为空串。
    """
    times = {p["period"]: p for p in data.get("periods", [])}
    lessons = []
    for c in sorted(data["courses"], key=lambda c: _periods_of(c["periods"])[:1] or [0]):
        if c["weekday"] != day.isoweekday() or (week and week not in _weeks_of(c["weeks"])):
            continue
        periods = _periods_of(c["periods"])
        start = times.get(str(periods[0]), {}).get("start", "") if periods else ""
        end = times.get(str(periods[-1]), {}).get("end", "") if periods else ""
        lessons.append({**c, "time": f"{start}-{end}" if start else "", "start": start, "end": end})
    return lessons


def _timetable(ctx: ToolContext, args: dict[str, Any]) -> Any:
    xnm, xqm = current_term(ctx.today)
    with jw_account.source_for(ctx.tenant_id, ctx.user_id) as jw:
        week = jw.current_week()
        data = jw.timetable(xnm, xqm)
    ctx._campus_id = data.get("campus_id", "")
    out = []
    for day, week_shift in _target_days(ctx, str(args.get("day") or "today"), args.get("weekday")):
        target_week = (week or 0) + week_shift
        lessons = lessons_on(data, day, target_week if week else None)
        out.append({"date": day.isoformat(), "weekday": f"星期{WEEKDAYS[day.weekday()]}", "week": target_week or None, "courses": lessons})
    ctx.cite("教务系统·个人课表")
    result: dict[str, Any] = {"current_week": week, "days": out}
    if not week:
        result["week_unknown"] = "教务没给出当前是第几周（可能在假期或新学期还没开始），上面列的是这个星期几所有的课，没按周次筛，请提醒学生以教务系统为准"
    rows = [{"day": f"{d['date'][5:]} {d['weekday']}", **c} for d in out for c in d["courses"]]
    if rows:
        result["display"] = ctx.table(
            "课表", [("day", "日期"), ("periods", "节次"), ("time", "时间"), ("course", "课程"), ("room", "教室"), ("teacher", "老师")], rows
        )
    else:
        result["note"] = "这段时间没有课"
    result["calendar_hint"] = "学生想把课表放进手机日历时，告诉他点右上角「工具」→「日历」，可以下载文件，也可以生成订阅链接（调课后手机里自动更新）"
    ctx.suggest("怎么把课表放进手机日历")
    return result


def _exams(ctx: ToolContext, args: dict[str, Any]) -> Any:
    xnm, xqm = _term(ctx, str(args.get("term") or "current"))
    with jw_account.source_for(ctx.tenant_id, ctx.user_id) as jw:
        exams = jw.exams(xnm or current_term(ctx.today)[0], xqm or current_term(ctx.today)[1])
    ctx.cite("教务系统·考试安排")
    result: dict[str, Any] = {"term": f"{xnm}-{xqm}", "exams": exams, "note": "" if exams else "这个学期还没有排考试，或考试安排未发布"}
    if exams:
        result["display"] = ctx.table(
            "考试安排", [("course", "课程"), ("time", "时间"), ("room", "考场"), ("seat", "座位号"), ("name", "考试")], exams
        )
        result["calendar_hint"] = "考试会随课表一起导出：「工具」→「日历」"
        ctx.suggest("帮我排一个考前复习计划", "我的考场怎么走")
    return result


_FAIL_WORDS = ("不及格", "不合格", "未通过", "挂科", "缺考", "作弊")
PASS_SCORE = 60


def is_failed(grade: dict[str, str]) -> bool:
    """成绩单里的一行是不是没通过：百分制低于 60，或者等级、文字写着不及格。绩点为 0 但成绩读不出来的不下结论。"""
    score = str(grade.get("score", "")).strip()
    if any(word in score for word in _FAIL_WORDS):
        return True
    try:
        return float(score) < PASS_SCORE
    except ValueError:
        return False


def gpa_by_term(grades: list[dict[str, str]]) -> list[dict[str, Any]]:
    """按学期算学分加权绩点；学分或绩点读不出来的行不参与，学期按出现顺序。"""
    sums: dict[str, list[float]] = {}
    for g in grades:
        try:
            credit, gpa = float(g["credit"]), float(g["gpa"])
        except (KeyError, TypeError, ValueError):
            continue
        entry = sums.setdefault(g.get("term", ""), [0.0, 0.0])
        entry[0] += credit
        entry[1] += credit * gpa
    return [
        {"term": term, "credits": round(credits, 1), "gpa": round(points / credits, 2) if credits else None}
        for term, (credits, points) in sums.items()
    ]


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
    terms = gpa_by_term(grades)
    failed = [g for g in grades if is_failed(g)]
    result: dict[str, Any] = {
        "count": len(grades),
        "credit_weighted_gpa": round(points / credits, 2) if credits else None,
        "gpa_by_term": terms,
        "grades": grades,
    }
    if failed:
        result["failed_courses"] = [{"term": g["term"], "course": g["course"], "score": g["score"], "credit": g["credit"]} for g in failed]
    if len(terms) > 1:
        ctx.suggest("每学期的绩点分别是多少")
    if failed:
        ctx.suggest("没通过的课要怎么补考重修")
    if grades:
        result["display"] = ctx.table(
            "成绩", [("term", "学期"), ("course", "课程"), ("score", "成绩"), ("credit", "学分"), ("gpa", "绩点"), ("type", "性质")], grades
        )
    else:
        result["note"] = "教务里这段时间还没有成绩（可能还没录入或没发布），不要编造任何分数"
    return result


def _graduation(ctx: ToolContext, args: dict[str, Any]) -> Any:
    with jw_account.source_for(ctx.tenant_id, ctx.user_id) as jw:
        data = jw.academia()
    ctx.cite("教务系统·学业情况")
    categories = data.get("categories") or []
    result: dict[str, Any] = {**data}
    if categories:
        result["display"] = ctx.table(
            "各类课程学分", [("name", "课程类别"), ("required", "要求"), ("earned", "已获"), ("missing", "还差")], categories
        )
    else:
        result["note"] = "教务没给出分类学分要求，只能看总体通过情况；具体还差哪门课以教务系统「学生学业情况查询」为准"
    if (data.get("plan") or {}).get("failed"):
        ctx.suggest("我有哪几门课没通过")
    if any((c.get("missing") or 0) > 0 for c in categories):
        ctx.suggest("按现在的进度，学分缺口怎么补")
    return result


def _free_rooms(ctx: ToolContext, args: dict[str, Any]) -> Any:
    xnm, xqm = current_term(ctx.today)
    periods = _periods_arg(args.get("periods") or []) or [1, 2]
    (day, week_shift), *_ = _target_days(ctx, str(args.get("day") or "today"), args.get("weekday"))
    building = str(args.get("building") or "").strip()
    with jw_account.source_for(ctx.tenant_id, ctx.user_id) as jw:
        week = jw.current_week() or 1
        campus = ctx._campus_id or jw.timetable(xnm, xqm).get("campus_id", "") or "1"
        rooms = jw.free_rooms(xnm, xqm, campus, week + week_shift, day.isoweekday(), periods)
    ctx.cite("教务系统·空教室")
    result: dict[str, Any] = {"date": day.isoformat(), "weekday": f"星期{WEEKDAYS[day.weekday()]}", "periods": periods}
    if building:
        # 正方的教室名常是「1-101」这种不带楼名的编号，楼名可能只在 building 里
        matched = [r for r in rooms if building in r["room"] or building in r.get("building", "")]
        if matched:
            rooms = matched
        else:
            result["building_not_found"] = f"没有名字含「{building}」的教学楼或教室，下面是全部空教室，请如实告诉学生"
    result.update({"count": len(rooms), "rooms": rooms[:ROOMS_FOR_MODEL]})
    if len(rooms) > ROOMS_FOR_MODEL:
        result["truncated"] = f"一共 {len(rooms)} 间，这里只列了前 {ROOMS_FOR_MODEL} 间"
    if rooms:
        result["display"] = ctx.table(
            "空教室", [("room", "教室"), ("building", "教学楼"), ("type", "类型"), ("seats", "座位")], rooms
        )
        if (building_name := _busiest_building(rooms)) and map_key(ctx.tenant_id):
            ctx.suggest(f"{building_name}怎么走")
        ctx.suggest("明天同一时段呢")
    else:
        result["note"] = "这个时段没有空教室"
    return result


def _busiest_building(rooms: list[dict[str, str]]) -> str:
    """空教室最多的那栋楼：值得问一句怎么走。教室名常是「1号教学楼 101」，楼名没单独给时取空格前面那段。"""
    counts: dict[str, int] = {}
    for room in rooms:
        name = room.get("building") or room.get("room", "").split(" ")[0]
        if name:
            counts[name] = counts.get(name, 0) + 1
    return max(counts, key=lambda name: counts[name]) if counts else ""


def _calendar(ctx: ToolContext, args: dict[str, Any]) -> Any:
    """今天是教学第几周；也能反查某一天是第几周、某一周是几号到几号。放假、开学这类校历安排不在教务接口里。"""
    school = school_ai(ctx.tenant_id)
    week: int | None = None
    if ctx.user_id and school.jw_mode in ("demo", "zhengfang"):
        try:
            with jw_account.source_for(ctx.tenant_id, ctx.user_id) as jw:
                week = jw.current_week()
        except (JwError, httpx.HTTPError):
            week = None  # 没绑定、教务连不上：只当不知道周次，日期照样回答
    today = ctx.today
    result: dict[str, Any] = {"today": today.isoformat(), "weekday": f"星期{WEEKDAYS[today.weekday()]}", "current_week": week}
    rule = "放假、开学、考试周、调休这类校历安排，用 search_knowledge 查学校上传的校历，查不到就如实说，不要凭印象回答"
    if not week:
        result["note"] = (
            "教务没给出当前是第几周（没绑定教务、可能在假期，或新学期还没开始），不要猜周次。" + rule
        )
        return result
    start = first_monday(today, week)
    result["week1_monday"] = start.isoformat()
    ctx.cite("教务系统·当前周次")
    asked_day = str(args.get("date") or "").strip()
    if asked_day:
        try:
            day = date.fromisoformat(asked_day)
            result["asked"] = {"date": day.isoformat(), "weekday": f"星期{WEEKDAYS[day.weekday()]}", "week": (day - start).days // 7 + 1}
        except ValueError:
            result["asked_error"] = "date 要写成 YYYY-MM-DD"
    asked_week = _int_arg(args.get("week"), 1, 30)
    if asked_week:
        monday = start + timedelta(weeks=asked_week - 1)
        result["asked_week"] = {"week": asked_week, "from": monday.isoformat(), "to": (monday + timedelta(days=6)).isoformat()}
    result["note"] = "周次按教务系统给的当前周推算，假期里不连续计周的学校可能有出入。" + rule
    return result


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
    if not any(school_tools.name_matches(name, p, school) for p in places):
        # 地图上常常只标了学校本身，没标校内的楼和门：至少能带新生先到学校
        campus = school_tools.campus(key, school)
        if campus:
            ctx.cite(f"高德地图·{campus.name}", campus.link, campus.address)
            result["campus"] = {"name": campus.name, "address": campus.address, "map_link": campus.link}
        mapped = school_tools.campus_places(key, school)
        if mapped:
            result["mapped_on_campus"] = [{"name": school_tools.short_name(p, school), "map_link": p.link} for p in mapped[:30]]
        result["note"] = (
            f"地图上没有名字对得上「{name}」的地点，places 里如果有结果也只是附近相近的，未必是要找的。"
            "mapped_on_campus 是学校在地图上标注过的校内地点，可以据此推荐最接近的"
            "（比如问的门没标，就说标了哪几个门）；校内具体位置以学校资料（search_knowledge）为准，也可以先导航到学校"
        )
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
        "jw_graduation", "查毕业进度",
        "查询当前学生本人的学业完成情况：平均绩点、培养计划内课程通过/未通过/未修/在读门数、各类课程要求学分和已获学分。问毕业还差多少学分、学业进度时用。",
        _obj(), _graduation,
    ),
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
_SEARCH_TOOL = Tool(
    "web_search", "联网搜索",
    "联网搜索。遇到会随时间变化的信息，或你不确定的事实，应当先搜再答。本校的通知和规章优先用 school_news 和 search_knowledge。",
    _obj({"query": {"type": "string"}}, ["query"]), _web_search,
)
_MAP_TOOLS = [
    Tool("campus_place", "找地点", "在地图上找学校里或学校附近的地点（教学楼、食堂、校门、医院、地铁站等），返回地址和导航链接。", _obj({"name": {"type": "string", "description": "地点名，如 图书馆、北门"}}, ["name"]), _place),
    Tool("walking_route", "规划步行路线", "规划学校里或附近两个地点之间的步行路线。", _obj({"origin": {"type": "string"}, "destination": {"type": "string"}}, ["origin", "destination"]), _route),
]
_CALENDAR_TOOL = Tool(
    "school_calendar", "查周次校历",
    "今天是星期几、教学第几周；也能查某个日期是第几周、第几周是几号到几号。问放假、开学、考试周等校历安排时一并用 search_knowledge。",
    _obj({
        "date": {"type": "string", "description": "要反查周次的日期，YYYY-MM-DD，可不填"},
        "week": {"type": "integer", "minimum": 1, "maximum": 30, "description": "要查日期范围的教学周，可不填"},
    }),
    _calendar,
)
_KNOWLEDGE_TOOL = Tool("search_knowledge", "查本校资料", "检索学校上传的资料（学生手册、规章、校园指南、地点介绍等）。", _obj({"query": {"type": "string"}}, ["query"]), _knowledge)


def available_tools(tenant_id: str, user_id: str, settings: WorkspaceSettings) -> list[Tool]:
    school = school_ai(tenant_id)
    tools = [_KNOWLEDGE_TOOL]
    if user_id and school.jw_mode in ("demo", "zhengfang"):
        tools += [*_JW_TOOLS, _CALENDAR_TOOL]  # 周次来自教务，没有教务就没有可靠的周次
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


def needs_tools(question: str, tool_names: set[str]) -> bool:
    """确定要查工具的问题：问本人教务数据、学校通知，或明显有时效性。

    这类问题交给分类模型去猜，一旦被判成闲聊，模型就会编出一份像真的成绩来。
    工具问答本身也带知识库检索，所以宁可多判进来：最多慢一点，不会答错。
    """
    if "jw_grades" in tool_names and any(word in question for word in _JW_WORDS):
        return True
    if "school_news" in tool_names and any(word in question for word in _NEWS_WORDS):
        return True
    return "web_search" in tool_names and (any(word in question for word in _TIMELY_WORDS) or bool(_YEAR.search(question)))


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


def read_audit(tenant_id: str, limit: int = AUDIT_KEEP) -> list[dict[str, Any]]:
    """本校最近的工具调用审计，新的在前。Redis 连不上就当没有，不拖垮调用方。"""
    try:
        rows = _redis().lrange(f"tool_audit:{tenant_id}", 0, limit - 1)
    except redis.RedisError as exc:
        logger.warning(f"[Agent] 审计读取失败: {exc}")
        return []
    found: list[dict[str, Any]] = []
    for raw in rows:
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict):
            found.append(item)
    return found


def tool_labels() -> dict[str, str]:
    """工具名 → 给人看的名字，使用情况页用。"""
    tools = [*_JW_TOOLS, *_NEWS_TOOLS, _SEARCH_TOOL, *_MAP_TOOLS, _KNOWLEDGE_TOOL]
    return {tool.name: tool.label for tool in tools}


ETA_SAMPLES = 3
ETA_WINDOW = 200
ETA_MAX_MS = 60_000


def typical_ms(tenant_id: str, name: str) -> int | None:
    """这个工具最近成功几次的耗时中位数，前端进度条用它估要等多久。样本太少就不估。"""
    spent = sorted(
        int(item.get("ms") or 0)
        for item in read_audit(tenant_id, ETA_WINDOW)
        if item.get("tool") == name and item.get("ok") is True
    )
    if len(spent) < ETA_SAMPLES:
        return None
    return min(spent[len(spent) // 2], ETA_MAX_MS)


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
        missing.append("个人课表/成绩/考试/毕业进度（" + ("访客不能查" if not ctx.user_id else "学校还没开通教务查询") + "）")
    if "campus_place" not in names:
        missing.append("地图导航（学校还没配地图 Key）")
    if "web_search" not in names:
        missing.append("校外的最新信息（" + ("本校要求只按资料回答" if ctx.settings.query_only else "学校没开联网搜索") + "）")
    gaps = f"\n目前查不了：{'；'.join(missing)}。被问到时如实说明，并建议去问辅导员或相关部门。" if missing else ""
    if ctx.refusal:
        gaps += f"\n- 本校要求只依据资料回答：答案必须来自工具结果，不能用你自己的知识补充；工具都没查到相关内容时，只回复「{ctx.refusal}」；但工具返回 error 或 how_to_fix 时，照常把那条提示转述给学生。"
    search_rule = (
        "\n- 涉及会随时间变化的信息（今年的政策、最新的日期和价格、排名、校外的活动和新闻），或者你对某个事实没有把握时，"
        "先调用 web_search 再回答，不要凭记忆作答。多搜一次的代价远小于说错。"
    ) if "web_search" in names else ""
    return f"""你是"{ctx.settings.assistant_name}"，{school}的智能校园助手，像热心的学长学姐，对刚入学、不认路的新生特别耐心。
今天是 {ctx.today.isoformat()}（星期{WEEKDAYS[ctx.today.weekday()]}）。

你可以调用工具。规则：
- 涉及具体事实（课程、时间、教室、成绩、通知内容、地点）必须以工具结果为准，不要编造；查不到就直说。
- 问本人课表、考试、成绩、空教室、毕业还差多少学分：调用教务工具。工具提示没绑定或过期时，告诉学生去「工具 → 教务账号」绑定。
- 问学校的通知、安排、活动：先用 school_news 找，需要细节再 read_notice 读正文。
- 问校园里的地方在哪、怎么走：先 search_knowledge 看学校资料，再用地图工具，给出导航链接。{search_rule}
- 工具结果里带 display 的，完整数据已经画成表格给学生看了，不要再逐行列出；没有 display 的多行结果（如多条通知、多个地点），用 Markdown 表格整理，不要省略行、不要改写数值。
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
            running: dict[str, Any] = {"name": call["name"], "label": label, "state": "running"}
            eta = typical_ms(ctx.tenant_id, call["name"]) if tool else None
            if eta is not None:
                running["eta_ms"] = eta
            on_tool(running)
            if tool:
                result, ok = run_tool(ctx, tool, call["arguments"])
            else:
                result, ok = json.dumps({"error": "没有这个工具"}, ensure_ascii=False), False
            on_tool({"name": call["name"], "label": label, "state": "done", "ok": ok})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    return "".join(parts)
