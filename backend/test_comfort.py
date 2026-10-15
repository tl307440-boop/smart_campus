"""情绪陪伴：高危识别、路由、陪伴回复、求助卡片。模型全部打桩:
    cd backend && pytest test_comfort.py
"""
from __future__ import annotations

from unittest.mock import MagicMock

import comfort
import graph_app
import pytest
from ai_config import ModelEndpoint
from workspace import WorkspaceSettings

ENDPOINT = ModelEndpoint("sk-school", "https://api.example.com/v1", "m", "school")
CONTACT = "心理健康教育中心 027-12345678"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(graph_app, "_delta_emitter", lambda: (lambda text: None))


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides) -> None:
    settings = WorkspaceSettings(**{**WorkspaceSettings(counseling_contact=CONTACT).__dict__, **overrides})
    monkeypatch.setattr(graph_app, "workspace_settings", lambda tenant_id: settings)


def _model(monkeypatch: pytest.MonkeyPatch, endpoint: ModelEndpoint | None = ENDPOINT) -> MagicMock:
    ds = MagicMock()
    ds.use_mock = False
    ds.stream_chat.return_value = iter(["我在", "听"])
    monkeypatch.setattr(graph_app, "get_deepseek", lambda: ds)
    monkeypatch.setattr(graph_app, "_chat_endpoint", lambda state: endpoint)
    return ds


def _state(question: str, intent: str, **extra) -> dict:
    return {"user_question": question, "intent": intent, "context": "", "tenant_id": "t1", "messages": [], **extra}


# ---------- 高危识别 ----------


@pytest.mark.parametrize("text", [
    "我真的不想活了", "我不想再活了", "不想再活下去", "活着 没意思", "我想死", "好想去死", "我写好了遗书",
    "想结束自己的生命", "昨晚又割腕了", "我死了算了", "我想从楼上跳下去", "站在天台想跳下去", "吃了一瓶安眠药",
    "我攒了很多安眠药", "活够了", "想一了百了", "想结束这一切", "用刀划手腕", "我想割手",
    "死了算了", "活着太累了，死了算了", "好累，不如去死", "活着没什么意思", "活着有什么意思", "我想自我了断",
    "我准备好遗书了", "难受得想死", "痛得想死", "不想活在这个世界上",
    "我想结束生命", "我打算结束生命", "我想割自己", "拿刀割自己", "我又割自己了", "划伤自己", "想用刀划自己",
    "我死了比较好", "我死了大家会更好", "我还是死了吧", "我是不是死了比较好", "我想服毒", "我想喝农药",
])
def test_crisis_phrases_are_caught(text: str) -> None:
    assert comfort.is_crisis(text)


@pytest.mark.parametrize("text", [
    "想死你了", "笑死我了", "今天累死了", "跳楼价清仓", "这题难死了", "死记硬背有用吗", "奖学金怎么申请",
    "周末不想活动", "我不想活动筋骨", "去欢乐谷坐跳楼机", "我不想死记硬背", "我想死磕这道题", "我的理想死了",
    "饿得想死", "烧炭烤肉怎么做", "不要伤害自己的眼睛", "这游戏死了算了", "划手机屏幕会坏吗", "吃安眠药能治失眠吗",
    "职业生涯规划手册在哪下载", "计划手账怎么做", "老师让我们比划手势", "上课比划手语", "我不想活成父母的样子",
    "不想活在别人的眼光里", "老师思想死板", "回想死去的外婆", "直接退课一了百了", "鲁迅遗书在哪本集子里",
    "企划手册模板", "区划手册", "刻划手法", "论文分割手稿", "预防自杀讲座在哪", "心理中心有自伤干预吗",
    "自杀式袭击的历史课", "软件生命周期怎么结束", "怎么划分自己的时间", "农药残留怎么检测",
    "如何规划自己大学四年", "怎么规划自己未来的职业", "合理计划自己每天的时间",
])
def test_everyday_phrases_are_not_crisis(text: str) -> None:
    assert not comfort.is_crisis(text)


@pytest.mark.parametrize(("text", "venting"), [("今天好emo", True), ("demo 怎么写", False), ("memo 和 emoji", False)])
def test_english_emo_is_matched_as_a_word(text: str, venting: bool) -> None:
    assert comfort.looks_like_venting(text) is venting


# ---------- 意图与路由 ----------


def test_crisis_skips_the_model_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    ds = _model(monkeypatch)
    client = MagicMock()
    monkeypatch.setattr(graph_app, "client_for", client)

    assert graph_app.intent_node({"user_question": "我不想活了", "tenant_id": "t1"}) == {"intent": "CRISIS"}
    client.assert_not_called()
    ds.stream_chat.assert_not_called()


def test_model_can_classify_venting(monkeypatch: pytest.MonkeyPatch) -> None:
    _model(monkeypatch)
    client = MagicMock()
    client.return_value.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content="COMFORT"))]
    monkeypatch.setattr(graph_app, "client_for", client)

    assert graph_app.intent_node({"user_question": "室友总孤立我", "tenant_id": "t1"}) == {"intent": "COMFORT"}


def test_keyword_fallback_without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _model(monkeypatch, endpoint=None)
    assert graph_app.intent_node({"user_question": "最近压力好大", "tenant_id": "t1"}) == {"intent": "COMFORT"}
    assert graph_app.intent_node({"user_question": "补考什么时候", "tenant_id": "t1"}) == {"intent": "RAG"}


def _tools(monkeypatch: pytest.MonkeyPatch, *names: str) -> MagicMock:
    tools = [MagicMock() for _ in names]
    for tool, name in zip(tools, names):
        tool.name = name
    monkeypatch.setattr(graph_app.agent, "available_tools", lambda tenant, user, settings: tools)
    client = MagicMock()
    client.return_value.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content="DIRECT"))]
    monkeypatch.setattr(graph_app, "client_for", client)
    return client


def test_personal_data_questions_skip_the_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    _model(monkeypatch)
    _settings(monkeypatch)
    client = _tools(monkeypatch, "jw_grades", "jw_timetable", "web_search")

    assert graph_app.intent_node({"user_question": "我上学期绩点多少", "tenant_id": "t1", "user_id": "u"}) == {"intent": "TOOL"}
    assert graph_app.intent_node({"user_question": "2026 年考研国家线出了吗", "tenant_id": "t1", "user_id": "u"}) == {"intent": "TOOL"}
    client.assert_not_called()


def test_short_follow_up_stays_on_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _model(monkeypatch)
    _settings(monkeypatch)
    client = _tools(monkeypatch, "jw_free_rooms", "campus_map")

    assert graph_app.intent_node({"user_question": "那四教呢", "tenant_id": "t1", "intent": "TOOL"}) == {"intent": "TOOL"}
    assert graph_app.intent_node({"user_question": "那四教呢", "tenant_id": "t1", "intent": "RAG"}) == {"intent": "DIRECT"}
    client.assert_called_once()


def test_venting_about_grades_is_not_routed_to_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _model(monkeypatch)
    _settings(monkeypatch)
    client = _tools(monkeypatch, "jw_grades", "jw_timetable")
    client.return_value.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content="COMFORT"))]

    assert graph_app.intent_node({"user_question": "成绩出来了好难过想哭", "tenant_id": "t1"}) == {"intent": "COMFORT"}


def _turns(count: int, size: int) -> list[dict]:
    messages = []
    for index in range(count):
        messages += [{"role": "user", "content": f"问{index}"}, {"role": "assistant", "content": "答" * size}]
    return messages


def test_history_is_cut_by_characters_but_keeps_min_turns() -> None:
    history = graph_app.budgeted_history(_turns(10, 1000), max_turns=10, max_chars=2500, min_turns=2)
    assert [m["content"] for m in history if m["role"] == "user"] == ["问8", "问9"]

    short = graph_app.budgeted_history(_turns(12, 10), max_turns=10, max_chars=6000, min_turns=2)
    assert len(short) == 20 and short[0] == {"role": "user", "content": "问2"}

    huge = graph_app.budgeted_history(_turns(3, 9000), max_turns=10, max_chars=100, min_turns=2)
    assert [m["role"] for m in huge] == ["user", "assistant", "user", "assistant"]


def test_summary_goes_before_recent_turns() -> None:
    history = graph_app.prompt_history({"messages": _turns(1, 5), "summary": "学生读大三，在找空教室"})
    assert history[0]["role"] == "system" and "在找空教室" in history[0]["content"]
    assert history[1:] == [{"role": "user", "content": "问0"}, {"role": "assistant", "content": "答" * 5}]
    assert graph_app.prompt_history({"messages": _turns(1, 5), "summary": "  "})[0]["role"] == "user"


def test_crisis_is_answered_even_when_school_only_answers_from_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, query_only=True)
    assert graph_app.route_query({"intent": "CRISIS", "tenant_id": "t1"}) == "generate"
    assert graph_app.route_query({"intent": "COMFORT", "tenant_id": "t1"}) == "retrieve"

    _settings(monkeypatch)
    assert graph_app.route_query({"intent": "COMFORT", "tenant_id": "t1"}) == "generate"


# ---------- 陪伴回复与求助卡片 ----------


def test_crisis_reply_uses_crisis_prompt_history_and_attaches_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, assistant_name="小文")
    ds = _model(monkeypatch)
    history = [{"role": "user", "content": "最近很累"}, {"role": "assistant", "content": "我在"}]

    out = graph_app.generate_node(_state("我不想活了", "CRISIS", messages=history))

    messages = ds.stream_chat.call_args.args[0]
    assert "伤害自己的念头" in messages[0]["content"] and CONTACT in messages[0]["content"]
    assert messages[1:3] == history and messages[-1]["content"] == "我不想活了"
    assert out["final_answer"] == "我在听"
    assert out["safety"] == {"level": "crisis", "hotlines": list(comfort.HOTLINES), "school": CONTACT}
    assert out["messages"][-1]["safety"] == out["safety"]


@pytest.mark.parametrize("endpoint_ok", [False, True])
def test_crisis_without_working_model_still_gets_fixed_reply(monkeypatch: pytest.MonkeyPatch, endpoint_ok: bool) -> None:
    _settings(monkeypatch)
    ds = _model(monkeypatch, endpoint=ENDPOINT if endpoint_ok else None)
    ds.stream_chat.side_effect = RuntimeError("401 sk-school")

    out = graph_app.generate_node(_state("我想死", "CRISIS"))

    assert "12356" in out["final_answer"] and CONTACT in out["final_answer"]
    assert "401" not in out["final_answer"] and "sk-school" not in out["final_answer"]
    assert out["safety"]["hotlines"][0]["phone"] == "12356"


def test_venting_gets_comfort_prompt_without_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    ds = _model(monkeypatch)

    out = graph_app.generate_node(_state("失恋了好难过", "COMFORT"))

    system = ds.stream_chat.call_args.args[0][0]["content"]
    assert "先共情" in system and "伤害自己的念头" not in system
    assert out["safety"] is None and "safety" not in out["messages"][-1]


def test_ordinary_turn_resets_last_turns_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    ds = _model(monkeypatch)
    ds.chat_with_context.return_value = iter(["答"])

    out = graph_app.generate_node(_state("奖学金", "RAG", context="资料", safety={"level": "crisis"}))

    assert out["safety"] is None


def test_emotion_failure_does_not_break_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    ds = _model(monkeypatch)
    ds.analyze_emotion_and_reply.side_effect = TypeError("the JSON object must be str, not NoneType")

    assert graph_app.emotion_node({"user_question": "我想死", "tenant_id": "t1"}) == {"emotion_data": None}


def test_mock_mode_gives_a_comfort_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, assistant_name="小文")
    ds = _model(monkeypatch)
    ds.use_mock = True

    out = graph_app.generate_node(_state("压力好大", "COMFORT"))

    assert "模拟回复" in out["final_answer"] and "小文" in out["final_answer"]
    ds.stream_chat.assert_not_called()


def test_finalize_waits_for_both_branches_on_later_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """情绪分析先跑完时，finalize 不能和 generate 同一步写 messages，也不能把情绪挂到上一轮的回答上。"""
    from langgraph.checkpoint.memory import MemorySaver

    turn = {"n": 0}

    def generate(state):
        turn["n"] += 1
        reply = {"role": "assistant", "content": f"答{turn['n']}"}
        return {"final_answer": reply["content"], "safety": None,
                "messages": [*state.get("messages", []), {"role": "user", "content": state["user_question"]}, reply]}

    monkeypatch.setattr(graph_app, "intent_node", lambda state: {"intent": "CRISIS"})
    monkeypatch.setattr(graph_app, "emotion_node", lambda state: {"emotion_data": {"risk_level": turn["n"] + 1}})
    monkeypatch.setattr(graph_app, "generate_node", generate)
    monkeypatch.setattr(graph_app, "get_checkpointer", MemorySaver)
    monkeypatch.setattr(graph_app, "_compiled_graph", None)
    graph = graph_app.build_graph()
    config = {"configurable": {"thread_id": "t"}}

    graph.invoke({"user_question": "一", "tenant_id": "t1"}, config)
    out = graph.invoke({"user_question": "二", "tenant_id": "t1"}, config)

    assistants = [m for m in out["messages"] if m["role"] == "assistant"]
    assert [m["content"] for m in assistants] == ["答1", "答2"]
    assert [m["emotion"]["risk_level"] for m in assistants] == [1, 2]


def test_finalize_adds_card_when_model_flags_high_risk(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    messages = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]

    high = graph_app.finalize_node({"tenant_id": "t1", "emotion_data": {"risk_level": 9}, "messages": messages})
    low = graph_app.finalize_node({"tenant_id": "t1", "emotion_data": {"risk_level": "3"}, "messages": messages})
    card = comfort.safety_card("已有")
    kept = graph_app.finalize_node({
        "tenant_id": "t1", "emotion_data": {"risk_level": 10},
        "messages": [messages[0], {**messages[1], "safety": card}],
    })

    assert high["safety"]["school"] == CONTACT and high["messages"][-1]["safety"] == high["safety"]
    assert "safety" not in low and "safety" not in low["messages"][-1]
    assert "safety" not in kept and kept["messages"][-1]["safety"] == card
