"""情感陪伴 + MCP 工具闭环 + 免密直达与跨天回访测试。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import agent
import auth
import chat_stream
import graph_app
import im_gateway
import im_scheduler
import jwt
import mcp_client
import pytest
import wellness_store
from ai_config import ModelEndpoint
from workspace import SchoolAi, WorkspaceSettings

ENDPOINT = ModelEndpoint("sk-school", "https://api.example.com/v1", "m", "school")


@pytest.fixture
def isolated_wellness_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_file = str(tmp_path / "wellness_test.sqlite3")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("WELLNESS_SQLITE_PATH", db_file)
    mcp_client.manager.close()
    yield db_file
    mcp_client.manager.close()


def test_real_stdio_mcp_roundtrip_and_host_tenant_isolation(
    isolated_wellness_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WELLNESS_SQLITE_PATH", isolated_wellness_db)
    tools = mcp_client.manager.list_tools()
    names = {t.name for t in tools}
    assert {
        "decompose_stress_to_todos",
        "list_my_todos",
        "complete_my_todo",
        "write_mood_journal",
        "read_mood_timeline",
        "activate_decompression_cabin",
    } <= names

    # 宿主侧必须已从给 LLM 的 schema 里剥离 tenant_id / user_id
    decompose_meta = next(t for t in tools if t.name == "decompose_stress_to_todos")
    assert "tenant_id" not in decompose_meta.llm_schema.get("properties", {})
    assert "user_id" not in decompose_meta.llm_schema.get("properties", {})

    # 即使参数里故意伪造 tenant_id/user_id，也必须被宿主层身份上下文覆盖
    res = mcp_client.manager.call_tool(
        "decompose_stress_to_todos",
        {
            "tenant_id": "attacker_tenant",
            "user_id": "attacker_user",
            "worry": "高数实验报告挤在一起",
            "tasks": [{"title": "写出实验目的和原理部分", "est_minutes": 20, "priority": "high", "due_label": "今晚"}],
        },
        tenant_id="t1",
        user_id="u_real",
    )
    assert res["ok"] is True and res["added_count"] == 1
    assert wellness_store.list_todos("attacker_tenant", "attacker_user") == []
    saved = wellness_store.list_todos("t1", "u_real")
    assert len(saved) == 1 and saved[0]["title"] == "写出实验目的和原理部分"

    # 完成打卡与减压舱更新均经由标准 MCP stdio 执行
    done = mcp_client.manager.call_tool("complete_my_todo", {"todo_id": saved[0]["id"]}, tenant_id="t1", user_id="u_real")
    assert done["ok"] is True and done["todo"]["status"] == "done"

    cabin = mcp_client.manager.call_tool(
        "activate_decompression_cabin",
        {"ambient_sound": "night_fire", "breathing_mode": "4-7-8", "focus_minutes": 25, "night_mode": True, "pet_mood": "night_care"},
        tenant_id="t1",
        user_id="u_real",
    )
    assert cabin["ok"] is True and cabin["cabin"]["ambient_sound"] == "night_fire"
    assert wellness_store.get_cabin_state("t1", "u_real")["night_mode"] is True


def test_mood_journal_sanitizes_pii_and_schedules_followup(isolated_wellness_db: str) -> None:
    entry = wellness_store.add_mood_journal(
        "t1",
        "u1",
        mood="焦虑",
        energy=3,
        stressor="答辩",
        summary="手机号 13812345678 身份证 420101200001011234 明天答辩好慌",
        followup_hours=12,
        followup_note="问问上午答辩顺利吗",
    )
    assert entry is not None
    assert "13812345678" not in entry["summary"] and "420101200001011234" not in entry["summary"]
    assert "[手机号]" in entry["summary"] and "[证件号]" in entry["summary"]
    assert entry["followup_at"]

    future = datetime.now(timezone.utc) + timedelta(hours=13)
    due = wellness_store.claim_due_followups(now=future)
    assert len(due) == 1 and due[0]["followup_note"] == "问问上午答辩顺利吗"
    # 已被原子领取标记为已发，不会重复领取
    assert wellness_store.claim_due_followups(now=future) == []


def test_context_aware_comfort_uses_companion_tools_and_emits_effects(
    isolated_wellness_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(graph_app, "_delta_emitter", lambda: (lambda text: None))
    monkeypatch.setattr(graph_app, "get_stream_writer", lambda: (lambda ev: events.append(ev)))
    monkeypatch.setattr(graph_app, "workspace_settings", lambda tid: WorkspaceSettings(assistant_name="小懒"))
    monkeypatch.setattr(graph_app, "_chat_endpoint", lambda st: ENDPOINT)
    ds = MagicMock()
    ds.use_mock = False
    monkeypatch.setattr(graph_app, "get_deepseek", lambda: ds)
    monkeypatch.setattr(agent, "school_ai", lambda tid: SchoolAi(jw_mode="demo"))
    monkeypatch.setattr(agent, "_mcp_tools_enabled", lambda: True)
    monkeypatch.setattr(agent, "_study_space_enabled", lambda: False)

    seen_prompts: list[str] = []

    def fake_run(ctx, endpoint, history, question, emit, on_tool, *, tools=None, custom_system_prompt=None):
        assert ctx.suppress_tables is True
        seen_prompts.append(custom_system_prompt or "")
        names = {t.name for t in (tools or [])}
        assert {"jw_timetable", "jw_exams", "decompose_stress_to_todos", "activate_decompression_cabin"} <= names
        ctx.emit_companion({"source": "mcp", "tool": "activate_decompression_cabin", "kind": "cabin_updated", "ambient_sound": "rain"})
        emit("我看到你明天还有两节课，今晚先听会儿雨声休息一下吧。")
        return "我看到你明天还有两节课，今晚先听会儿雨声休息一下吧。"

    monkeypatch.setattr(graph_app.agent, "run", fake_run)

    out = graph_app.generate_node({
        "user_question": "压力好大完全睡不着",
        "intent": "COMFORT",
        "context": "",
        "tenant_id": "t1",
        "user_id": "u1",
        "messages": [],
    })

    assert "明天还有两节课" in out["final_answer"]
    assert "先共情接住" in seen_prompts[0]
    assert out["companion_effects"][0]["ambient_sound"] == "rain"
    assert any(e.get("companion", {}).get("pet_mood") == "listening" for e in events)
    assert any(e.get("companion", {}).get("ambient_sound") == "rain" for e in events)


def test_sse_stream_forwards_companion_events() -> None:
    stream = [
        ("custom", {"companion": {"kind": "cabin_updated", "ambient_sound": "rain"}}),
        ("custom", {"delta": "好"}),
        ("updates", {"generate": {"final_answer": "好", "companion_effects": [{"kind": "cabin_updated"}]}}),
    ]
    frames = list(chat_stream.sse_from_graph_stream(stream))
    assert any(f.startswith("event: companion\n") and '"ambient_sound": "rain"' in f for f in frames)
    done_frame = frames[-1]
    assert done_frame.startswith("event: done\n")
    payload = json.loads(done_frame.split("data: ", 1)[1])
    assert payload["companion"] == [{"kind": "cabin_updated"}]


def test_magic_link_im_command_and_scheduler_followup(
    isolated_wellness_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-for-magic-link-32bytes!!")
    monkeypatch.setenv("PUBLIC_WEB_ORIGIN", "http://47.111.163.151")

    url = auth.build_magic_url("t1", "u1", next_path="/?view=archive")
    assert url.startswith("http://47.111.163.151/magic?t=")
    token = url.split("?t=", 1)[1].split("&next=", 1)[0]
    from urllib.parse import unquote
    claims = jwt.decode(unquote(token), "test-secret-for-magic-link-32bytes!!", algorithms=["HS256"], issuer="zhiyue-campus")
    assert claims["sub"] == "u1" and claims["tid"] == "t1" and claims["purpose"] == "magic_link"

    wellness_store.add_todos("t1", "u1", [{"title": "背完 10 个核心公式", "est_minutes": 15}], source="mcp_decompose")
    binding = im_gateway.ImBinding(
        id="b1", tenant_id="t1", user_id="u1", platform="wecom", external_id="zhangsan",
        display_name="张三", morning_digest=True, digest_time="07:30",
        grade_alert=True, ticket_alert=True, news_keywords="", last_grade_hash="", companion_followup=True,
    )
    monkeypatch.setattr(im_gateway, "resolve_binding", lambda p, ext, tenant_id_hint="": binding)
    reply = im_gateway.handle_incoming_message("wecom", "zhangsan", "减压")
    assert "背完 10 个核心公式" in reply
    assert "http://47.111.163.151/magic?t=" in reply

    # 测试跨天回访在清醒时段发送，深夜静默不打扰
    wellness_store.add_mood_journal(
        "t1", "u1", mood="紧张", energy=4, stressor="考试", summary="明天考线代",
        followup_hours=1, followup_note="线代考试加油呀",
    )
    sent: list[str] = []
    monkeypatch.setattr(im_gateway, "send_im_message", lambda t, p, ext, msg: sent.append(msg) or True)
    future_night = datetime(2026, 11, 19, 2, 0, tzinfo=timezone.utc)
    assert im_scheduler.process_companion_followups([binding], future_night) == 0

    future_day = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    assert im_scheduler.process_companion_followups([binding], future_day) == 1
    assert len(sent) == 1 and "线代考试加油呀" in sent[0] and "/magic?t=" in sent[0]
