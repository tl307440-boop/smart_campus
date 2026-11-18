"""智悦校园 · 情感陪伴与生活减压 MCP Server（基于官方 mcp.server.fastmcp）。

可作为标准 stdio MCP Server 被后端 `mcp_client` 挂载，也可直接接入外部 MCP 客户端
（如 Claude Desktop / Cursor / Cherry Studio）：
    python -m mcp_wellness
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import wellness_store
from mcp.server.fastmcp import FastMCP

# stdio 传输占用 stdout 发 JSON-RPC 帧；日志必须严格走 stderr
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

mcp = FastMCP(
    "zhiyue-wellness",
    instructions=(
        "智悦校园情感陪伴与减压工具集：用于将高压学业困扰拆解为 15-30 分钟的微待办、"
        "记录与回溯脱敏情绪轨迹手账、预约次日温和回访，以及开启白噪音与 4-7-8 呼吸减压舱。"
    ),
    log_level="WARNING",
)


@mcp.tool(
    title="拆解压力为微待办",
    description=(
        "把令学生焦虑的大任务、DDL 堆积或无从下手的事拆解成 1~5 个 15~30 分钟可立即执行的小微待办并保存。"
        "tasks 列表中每项为对象，含 title（简洁动词开头）、est_minutes（整数 5~60）、"
        "priority（high/medium/low）、due_label（如 今晚/明天上午）、detail（一句话起步提示）。"
    ),
)
def decompose_stress_to_todos(
    tenant_id: str,
    user_id: str,
    worry: str,
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    created = wellness_store.add_todos(tenant_id, user_id, tasks, source="mcp_decompose")
    return {
        "ok": True,
        "worry": wellness_store.sanitize_text(worry, max_chars=120),
        "added_count": len(created),
        "todos": created,
        "ui_effect": {
            "kind": "todos_added",
            "count": len(created),
            "pet_mood": "cheering",
        },
    }


@mcp.tool(
    title="查看我的微待办",
    description="查看学生当前的待办微任务列表（status 可选 pending / done / all），在陪伴或做规划前了解他手头还有什么事。",
)
def list_my_todos(
    tenant_id: str,
    user_id: str,
    status: str = "pending",
) -> dict[str, Any]:
    items = wellness_store.list_todos(tenant_id, user_id, status=status, limit=20)
    return {
        "ok": True,
        "status": status,
        "count": len(items),
        "todos": items,
    }


@mcp.tool(
    title="打卡完成微待办",
    description="当学生说自己完成某件微待办或想打卡一项小成就时调用，标记为已完成并给予正向情绪鼓励。",
)
def complete_my_todo(
    tenant_id: str,
    user_id: str,
    todo_id: str,
) -> dict[str, Any]:
    updated = wellness_store.toggle_todo(tenant_id, user_id, todo_id, done=True)
    if updated is None:
        return {"ok": False, "error": "没有找到对应的微待办"}
    return {
        "ok": True,
        "todo": updated,
        "ui_effect": {
            "kind": "todo_completed",
            "pet_mood": "cheering",
        },
    }


@mcp.tool(
    title="记录树洞情绪手账",
    description=(
        "为学生记录一条脱敏的情绪轨迹手账（禁止写入证件号、手机号或临床疾病诊断）。"
        "mood 填情绪词（如 焦虑/疲惫/委屈/松了口气/开心）；energy 为 1~10 的能量分（1=极耗竭，10=元气满满）；"
        "stressor 填压力源（如 学业/考试/DDL/人际/作息/未来）；summary 为温柔客观的一句话小结；"
        "如果学生提到明天/之后有令人紧张的事件（如答辩/考试/面试），可设 followup_hours（如 18）和 followup_note 触发次日企微主动关心。"
    ),
)
def write_mood_journal(
    tenant_id: str,
    user_id: str,
    mood: str,
    energy: int,
    stressor: str,
    summary: str,
    followup_note: str = "",
    followup_hours: int = 0,
) -> dict[str, Any]:
    hours = followup_hours if followup_hours and followup_hours > 0 else None
    entry = wellness_store.add_mood_journal(
        tenant_id,
        user_id,
        mood=mood,
        energy=energy,
        stressor=stressor,
        summary=summary,
        source="mcp_journal",
        followup_hours=hours,
        followup_note=followup_note,
    )
    if entry is None:
        return {"ok": False, "error": "写入情绪手账失败"}
    return {
        "ok": True,
        "journal": entry,
        "followup_scheduled": bool(entry.get("followup_at")),
        "ui_effect": {
            "kind": "journal_saved",
            "energy": entry["energy"],
            "mood": entry["mood"],
        },
    }


@mcp.tool(
    title="读取近期情绪轨迹",
    description="读取学生最近 days 天（默认 7 天）的情绪能量分、压力源分布与手账记录，用于在陪伴时理解连续几天的状态变化。",
)
def read_mood_timeline(
    tenant_id: str,
    user_id: str,
    days: int = 7,
) -> dict[str, Any]:
    items = wellness_store.list_mood_journals(tenant_id, user_id, days=days, limit=20)
    avg = round(sum(i["energy"] for i in items) / len(items), 1) if items else None
    return {
        "ok": True,
        "days": days,
        "count": len(items),
        "avg_energy": avg,
        "journals": items,
    }


@mcp.tool(
    title="开启减压伴学舱",
    description=(
        "为情绪紧绷、深夜失眠或准备静下心自习的学生即时开启网页/手机端的减压伴学舱氛围。"
        "ambient_sound 可选: rain(微雨白噪音) / library(自习室翻书声) / night_fire(暖夜炉火) / waves(海浪) / none；"
        "breathing_mode 可选: 4-7-8(睡前/降焦虑呼吸) / box(专注方块呼吸) / none；"
        "pet_mood 可选: listening / night_care / relaxed / cheering / normal；"
        "focus_minutes 建议 15~45。"
    ),
)
def activate_decompression_cabin(
    tenant_id: str,
    user_id: str,
    ambient_sound: str = "rain",
    breathing_mode: str = "4-7-8",
    focus_minutes: int = 25,
    night_mode: bool = True,
    pet_mood: str = "night_care",
    note: str = "",
) -> dict[str, Any]:
    state = wellness_store.update_cabin_state(
        tenant_id,
        user_id,
        ambient_sound=ambient_sound,
        breathing_mode=breathing_mode,
        focus_minutes=focus_minutes,
        night_mode=night_mode,
        pet_mood=pet_mood,
        note=note,
    )
    return {
        "ok": True,
        "cabin": state,
        "ui_effect": {
            "kind": "cabin_updated",
            **state,
        },
    }


@mcp.resource(
    "wellness://breathing-guide",
    name="4-7-8 呼吸与情绪着陆指南",
    description="供助眠、焦虑缓解时参考的 4-7-8 呼吸节奏与 5-4-3-2-1 感官着陆练习说明。",
)
def breathing_guide_resource() -> str:
    return (
        "# 4-7-8 呼吸节律指南\n"
        "1. 用鼻子安静吸气 4 秒；\n"
        "2. 屏住呼吸 7 秒；\n"
        "3. 噘嘴缓慢吐气 8 秒，像轻轻吹灭一根蜡烛；\n"
        "4. 重复 3~4 轮即可明显降低交感神经兴奋。\n\n"
        "# 5-4-3-2-1 感官着陆法\n"
        "说出眼前看到的 5 样东西、触摸到的 4 种质感、听到的 3 个声音、闻到的 2 种气味、感受到的 1 个此刻支持。\n"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
