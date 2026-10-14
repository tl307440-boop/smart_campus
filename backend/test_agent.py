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
    assert {"jw_timetable", "jw_grades", "school_news", "read_notice", "campus_place", "walking_route", "web_search"} <= names
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
