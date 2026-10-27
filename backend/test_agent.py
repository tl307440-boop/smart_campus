"""工具问答：周次与日期换算、按配置开放工具、函数调用循环、出错兜底与审计。"""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import agent
import jw_account
import pytest
from ai_config import ModelEndpoint, ToolKey
from jw_zhengfang import JwAuthError
from workspace import SchoolAi, WorkspaceSettings

ENDPOINT = ModelEndpoint("k", "https://api.example.com/v1", "m", "school")


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key: str, start: int, end: int) -> None:
        self.lists[key] = self.lists[key][start:end + 1]

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self.lists.get(key, [])[start:end + 1]


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(agent, "_redis", lambda: fake)
    return fake


def _ctx(today: date = date(2026, 10, 12), user_id: str = "u1") -> agent.ToolContext:
    return agent.ToolContext("t1", user_id, WorkspaceSettings(), lambda q: ("", []), today=today)


@pytest.mark.parametrize(
    ("zcd", "weeks"),
    [("1-4周", {1, 2, 3, 4}), ("2-8周(双)", {2, 4, 6, 8}), ("1-5周(单),9周", {1, 3, 5, 9}), ("3周", {3}), ("1-2周，5-6周", {1, 2, 5, 6})],
)
def test_weeks_of(zcd: str, weeks: set[int]) -> None:
    assert agent._weeks_of(zcd) == weeks


def test_target_days() -> None:
    wednesday = _ctx(date(2026, 10, 14))
    assert agent._target_days(wednesday, "tomorrow", None) == [(date(2026, 10, 15), 0)]
    assert agent._target_days(wednesday, "today", 1) == [(date(2026, 10, 19), 1)]  # 周一已过，指下周一
    assert agent._target_days(wednesday, "next_week", 5) == [(date(2026, 10, 23), 1)]
    assert len(agent._target_days(wednesday, "this_week", None)) == 7
    assert agent._target_days(_ctx(date(2026, 10, 18)), "tomorrow", None) == [(date(2026, 10, 19), 1)]


def test_timetable_filters_by_weekday_and_week(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="demo"))
    monday = agent._timetable(_ctx(date(2026, 10, 12)), {"day": "today"})
    assert monday["current_week"] == 7
    lessons = monday["days"][0]["courses"]
    assert [c["course"] for c in lessons] == ["高等数学（演示）", "大学英语（演示）"]
    assert lessons[0]["time"] == "08:00-09:40"
    # 线性代数只在双周上，第 7 周的周五没有它
    friday = agent._timetable(_ctx(date(2026, 10, 12)), {"day": "this_week", "weekday": 5})
    assert friday["days"][0]["courses"] == []
    next_friday = agent._timetable(_ctx(date(2026, 10, 12)), {"day": "next_week", "weekday": 5})
    assert [c["course"] for c in next_friday["days"][0]["courses"]] == ["线性代数（演示）"]


def test_bad_model_args_are_coerced() -> None:
    wednesday = _ctx(date(2026, 10, 14))
    assert agent._target_days(wednesday, "today", 8) == [(date(2026, 10, 14), 0)]
    assert agent._target_days(wednesday, "today", "5") == [(date(2026, 10, 16), 0)]
    assert agent._periods_arg(["1-2", 5, "x", 99]) == [1, 2, 5]
    assert agent._periods_arg("3-4") == [3, 4]


def test_unknown_week_is_flagged_and_ungraded_courses_skip_gpa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="demo"))
    monkeypatch.setattr(jw_account.DemoJw, "current_week", lambda self: None)
    assert "week_unknown" in agent._timetable(_ctx(), {"day": "today"})
    grades = [{"credit": "4", "gpa": "4.0"}, {"credit": "2", "gpa": ""}]
    monkeypatch.setattr(jw_account.DemoJw, "grades", lambda self, xnm="", xqm="": grades)
    assert agent._grades(_ctx(), {})["credit_weighted_gpa"] == 4.0


def test_multi_row_results_become_tables_and_model_is_told_not_to_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="demo"))
    ctx = _ctx(date(2026, 10, 12))
    grades = agent._grades(ctx, {})
    assert grades["display"] == agent.TABLE_SHOWN
    table = ctx.tables[0]
    assert table["title"] == "成绩" and table["total"] == 2
    assert [c["key"] for c in table["columns"]] == ["term", "course", "score", "credit", "gpa", "type"]
    assert table["rows"][0]["course"] == "高等数学（演示）" and table["rows"][0]["score"] == "88"


def test_table_drops_columns_that_are_empty_everywhere() -> None:
    ctx = _ctx()
    ctx.table("空教室", [("room", "教室"), ("building", "教学楼")], [{"room": "1-101", "building": ""}, {"room": "1-102"}])
    assert ctx.tables[0]["columns"] == [{"key": "room", "label": "教室"}]
    assert ctx.tables[0]["rows"] == [{"room": "1-101"}, {"room": "1-102"}]


def test_free_rooms_says_so_when_building_matches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="demo"))
    ctx = _ctx(date(2026, 10, 12))
    hit = agent._free_rooms(ctx, {"building": "1号教学楼"})
    assert hit["count"] == 3 and "building_not_found" not in hit
    miss = agent._free_rooms(_ctx(date(2026, 10, 12)), {"building": "图书馆"})
    assert miss["count"] == 3 and "图书馆" in miss["building_not_found"]


def test_free_rooms_tells_model_about_truncation_but_table_keeps_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="demo"))
    many = [{"room": f"1-{n}", "building": "", "type": "多媒体", "seats": "60"} for n in range(100, 160)]
    monkeypatch.setattr(jw_account.DemoJw, "free_rooms", lambda self, *args: many)
    ctx = _ctx(date(2026, 10, 12))
    result = agent._free_rooms(ctx, {})
    assert len(result["rooms"]) == agent.ROOMS_FOR_MODEL and "60" in result["truncated"]
    assert len(ctx.tables[0]["rows"]) == 60


def test_empty_grades_carry_a_no_fabrication_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="demo"))
    monkeypatch.setattr(jw_account.DemoJw, "grades", lambda self, xnm="", xqm="": [])
    ctx = _ctx()
    result = agent._grades(ctx, {})
    assert "不要编造" in result["note"] and ctx.tables == []


def test_graduation_progress_reports_category_credits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="demo"))
    ctx = _ctx()
    result = agent._graduation(ctx, {})
    assert result["plan"]["failed"] == 1 and result["display"] == agent.TABLE_SHOWN
    assert ctx.tables[0]["rows"][1] == {"name": "专业必修课（演示）", "required": "60.0", "earned": "35.5", "missing": "24.5"}
    assert ctx.sources[0]["source"] == "教务系统·学业情况"


@pytest.mark.parametrize(
    ("question", "names", "expected"),
    [
        ("我这学期成绩怎么样", {"jw_grades"}, True),
        ("明天有课吗", {"jw_grades"}, True),
        ("哪里有空教室", {"jw_grades"}, True),
        ("我成绩怎么样", set(), False),  # 学校没开教务：交给分类器
        ("今年国家奖学金什么时候评", {"web_search"}, True),
        ("2027 年寒假几号放", {"web_search"}, True),
        ("你好呀", {"jw_grades", "web_search", "school_news"}, False),
        ("最近有什么讲座", {"school_news"}, True),
    ],
)
def test_needs_tools(question: str, names: set[str], expected: bool) -> None:
    assert agent.needs_tools(question, names) is expected


def test_prompt_mentions_web_search_only_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "school_name", lambda tenant_id: "测试大学")
    search = agent.Tool("web_search", "", "", agent._obj(), lambda c, a: None)
    assert "先调用 web_search" in agent.system_prompt(_ctx(), [search])
    prompt = agent.system_prompt(_ctx(), [])
    assert "先调用 web_search" not in prompt and "学校没开联网搜索" in prompt


def test_query_only_school_gets_refusal_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "school_name", lambda tenant_id: "测试大学")
    ctx = _ctx()
    assert "只依据资料" not in agent.system_prompt(ctx, [])
    ctx.refusal = "抱歉，没有找到。"
    assert "只回复「抱歉，没有找到。」" in agent.system_prompt(ctx, [])


def test_unmapped_campus_place_falls_back_to_school_location(monkeypatch: pytest.MonkeyPatch) -> None:
    campus = agent.school_tools.Place("测试学院", "某路 1 号", 114.4, 30.72)
    monkeypatch.setattr(agent, "map_key", lambda tenant_id: ToolKey("m", "school"))
    monkeypatch.setattr(agent, "school_name", lambda tenant_id: "测试学院")
    monkeypatch.setattr(agent.school_tools, "find_places", lambda key, school, name, limit=5: [])
    monkeypatch.setattr(agent.school_tools, "campus", lambda key, school: campus)
    gate = agent.school_tools.Place("测试学院(东门)", "", 114.41, 30.72)
    monkeypatch.setattr(agent.school_tools, "campus_places", lambda key, school: [gate])
    ctx = _ctx()
    result = agent._place(ctx, {"name": "南门"})
    assert result["places"] == [] and result["campus"]["map_link"] == campus.link
    assert result["mapped_on_campus"] == [{"name": "东门", "map_link": gate.link}]
    assert "学校资料" in result["note"] and ctx.sources[0]["url"] == campus.link


def _gate(monkeypatch: pytest.MonkeyPatch, school: SchoolAi, has_map: bool, has_search: bool) -> None:
    monkeypatch.setattr(agent, "school_ai", lambda tenant_id: school)
    monkeypatch.setattr(agent, "map_key", lambda tenant_id: ToolKey("m", "school") if has_map else None)
    monkeypatch.setattr(agent, "search_key", lambda tenant_id: ToolKey("s", "platform") if has_search else None)


def test_tools_follow_school_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _gate(monkeypatch, SchoolAi(), False, False)
    assert [t.name for t in agent.available_tools("t1", "u1", WorkspaceSettings())] == ["search_knowledge"]
    assert not agent.has_live_tools("t1", "u1", WorkspaceSettings())

    _gate(monkeypatch, SchoolAi(jw_mode="demo", news_urls=("https://jwc.test.edu.cn/",)), True, True)
    names = {t.name for t in agent.available_tools("t1", "u1", WorkspaceSettings())}
    assert {"jw_timetable", "jw_grades", "jw_graduation", "school_news", "read_notice", "campus_place", "walking_route", "web_search"} <= names
    # 挂件访客没有账号：没有教务工具
    assert not any(n.startswith("jw_") for n in (t.name for t in agent.available_tools("t1", "", WorkspaceSettings())))
    # 只按资料回答：不开放联网搜索
    assert "web_search" not in {t.name for t in agent.available_tools("t1", "u1", WorkspaceSettings(query_only=True))}


def test_run_tool_turns_errors_into_hints(audit: FakeRedis) -> None:
    def unbound(ctx, args):
        raise JwAuthError("你还没有绑定教务账号")

    def broken(ctx, args):
        raise RuntimeError("boom secret detail")

    text, ok = agent.run_tool(_ctx(), agent.Tool("jw_grades", "查成绩", "", agent._obj(), unbound), '{"term": "all"}')
    assert not ok and "教务账号" in json.loads(text)["how_to_fix"]
    text, ok = agent.run_tool(_ctx(), agent.Tool("x", "x", "", agent._obj(), broken), "not json")
    assert not ok and json.loads(text) == {"error": "这项查询暂时不可用"}
    entries = [json.loads(e) for e in audit.lists["tool_audit:t1"]]
    assert entries[1]["tool"] == "jw_grades" and entries[1]["args"] == {"term": "all"} and entries[1]["user"] == "u1"
    assert "boom" not in json.dumps(entries)


def _chunk(content: str | None = None, call: dict[str, Any] | None = None) -> SimpleNamespace:
    tool_calls = None
    if call:
        tool_calls = [SimpleNamespace(index=0, id=call.get("id"), function=SimpleNamespace(name=call.get("name"), arguments=call.get("arguments")))]
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))])


class FakeModel:
    """第一轮流式吐出一个分两段的工具调用，第二轮给出回答。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            return iter([
                _chunk("我查一下。"),
                _chunk(call={"id": "c1", "name": "jw_timetable", "arguments": '{"day":'}),
                _chunk(call={"arguments": '"today"}'}),
            ])
        return iter([_chunk("今天有 2 节课"), _chunk("：高等数学、大学英语。")])


def test_agent_loop_calls_tool_then_answers(monkeypatch: pytest.MonkeyPatch, audit: FakeRedis) -> None:
    _gate(monkeypatch, SchoolAi(jw_mode="demo"), False, False)
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="demo"))
    monkeypatch.setattr(agent, "school_name", lambda tenant_id: "测试学院")
    model = FakeModel()
    monkeypatch.setattr(agent, "client_for", lambda endpoint: model)
    emitted: list[str] = []
    events: list[dict[str, Any]] = []
    ctx = _ctx(date(2026, 10, 12))

    answer = agent.run(ctx, ENDPOINT, [], "我今天有什么课", emitted.append, events.append)

    assert answer == "我查一下。\n\n今天有 2 节课：高等数学、大学英语。"
    assert "".join(emitted) == answer
    assert events == [
        {"name": "jw_timetable", "label": "查课表", "state": "running"},
        {"name": "jw_timetable", "label": "查课表", "state": "done", "ok": True},
    ]
    second = model.requests[1]["messages"]
    assert second[-2]["tool_calls"][0]["function"] == {"name": "jw_timetable", "arguments": '{"day":"today"}'}
    assert second[-1]["role"] == "tool" and "高等数学（演示）" in second[-1]["content"]
    assert "测试学院" in second[0]["content"] and "2026-10-12" in second[0]["content"]
    assert ctx.sources[0]["source"] == "教务系统·个人课表"
    assert len(audit.lists["tool_audit:t1"]) == 1


def test_agent_stops_calling_tools_after_max_rounds(monkeypatch: pytest.MonkeyPatch, audit: FakeRedis) -> None:
    _gate(monkeypatch, SchoolAi(), False, False)
    monkeypatch.setattr(agent, "school_name", lambda tenant_id: "")
    requests: list[dict[str, Any]] = []

    def create(**kwargs: Any):
        requests.append(kwargs)
        if kwargs.get("tool_choice") == "none":
            return iter([_chunk("资料里没找到。")])
        return iter([_chunk(call={"id": f"c{len(requests)}", "name": "search_knowledge", "arguments": '{"query": "图书馆"}'})])

    monkeypatch.setattr(agent, "client_for", lambda endpoint: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    answer = agent.run(_ctx(), ENDPOINT, [], "图书馆在哪", lambda t: None, lambda e: None)
    assert answer == "资料里没找到。"
    assert len(requests) == agent.MAX_ROUNDS + 1


def _seed_audit(audit: FakeRedis, tool: str, spent: list[int], ok: bool = True) -> None:
    for ms in spent:
        audit.lpush("tool_audit:t1", json.dumps({"ts": 1, "user": "u1", "tool": tool, "args": {}, "ok": ok, "error": "", "ms": ms}))


def test_typical_ms_is_the_median_of_recent_successes(audit: FakeRedis) -> None:
    _seed_audit(audit, "jw_free_rooms", [900, 100, 300])
    _seed_audit(audit, "jw_free_rooms", [9999], ok=False)  # 失败的不算，免得把预估拉长
    assert agent.typical_ms("t1", "jw_free_rooms") == 300
    assert agent.typical_ms("t1", "jw_grades") is None


def test_typical_ms_needs_enough_samples(audit: FakeRedis) -> None:
    _seed_audit(audit, "jw_exams", [500, 600])
    assert agent.typical_ms("t1", "jw_exams") is None


def test_read_audit_skips_garbage_rows(audit: FakeRedis) -> None:
    audit.lpush("tool_audit:t1", "not json")
    audit.lpush("tool_audit:t1", json.dumps(["list"]))
    _seed_audit(audit, "jw_exams", [100])
    assert [item["tool"] for item in agent.read_audit("t1")] == ["jw_exams"]


def test_tool_labels_cover_every_tool() -> None:
    labels = agent.tool_labels()
    assert labels["jw_free_rooms"] == "查空教室"
    assert {"jw_timetable", "school_news", "web_search", "campus_place", "search_knowledge"} <= labels.keys()


def test_running_event_carries_the_expected_wait(monkeypatch: pytest.MonkeyPatch, audit: FakeRedis) -> None:
    _gate(monkeypatch, SchoolAi(jw_mode="demo"), False, False)
    monkeypatch.setattr(jw_account, "school_ai", lambda tenant_id: SchoolAi(jw_mode="demo"))
    monkeypatch.setattr(agent, "school_name", lambda tenant_id: "")
    monkeypatch.setattr(agent, "client_for", lambda endpoint: FakeModel())
    _seed_audit(audit, "jw_timetable", [2000, 2500, 3000])
    events: list[dict[str, Any]] = []

    agent.run(_ctx(), ENDPOINT, [], "我今天有什么课", lambda text: None, events.append)

    assert events[0] == {"name": "jw_timetable", "label": "查课表", "state": "running", "eta_ms": 2500}
    assert "eta_ms" not in events[1]
