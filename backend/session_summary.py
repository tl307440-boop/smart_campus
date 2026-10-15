"""滚动摘要：把一次对话里早于提示词窗口的轮次压成一段事实摘要，存在 chat_sessions.summary。

每成功一轮后在后台线程里检查一次，攒够 SUMMARY_BATCH_TURNS 轮才调一次模型，
不拖慢回答。失败只记日志，摘要停在旧版本，下一轮再试。

摘要不覆盖最近 KEEP_TURNS 轮：提示词窗口最多放 MAX_PROMPT_TURNS 轮，
留出一个批次的重叠，摘要和窗口之间才不会漏掉中间的轮次。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import chat_history
from ai_config import ModelEndpoint, client_for, scrub
from graph_app import MAX_PROMPT_TURNS
from logging_config import get_logger

logger = get_logger(__name__)

SUMMARY_BATCH_TURNS = 5
KEEP_TURNS = max(1, MAX_PROMPT_TURNS - SUMMARY_BATCH_TURNS)
SUMMARY_MAX_CHARS = 800
MESSAGE_MAX_CHARS = 1500
SUMMARY_TEMPERATURE = 0.2
SUMMARY_MAX_TOKENS = 1200
SUMMARY_TIMEOUT_SECONDS = 60.0
_WORKERS = 2

SUMMARY_PROMPT = (
    "你在帮校园助手整理一段对话的记录。把【旧摘要】和【新的对话】合并成一份新的摘要，"
    f"不超过 {SUMMARY_MAX_CHARS} 字，分条写，用第三人称称呼学生。\n"
    "只保留以后答题用得上的事实：学生自己说过的身份信息（专业、年级、校区等）、"
    "正在办的事和进展、助手已经给出的结论和链接、学生明确表达过的偏好。\n"
    "不保留：寒暄、情绪起伏、一次性的措辞、成绩课表这类会变的数字（以后要用会重新查）。\n"
    "旧摘要里仍然成立的内容要保留，被新对话推翻的以新的为准。只输出摘要本身。"
)

_executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="session-summary")


def _transcript(messages: list[dict]) -> str:
    lines = []
    for message in messages:
        speaker = "学生" if message.get("role") == "user" else "助手"
        content = (message.get("content") or "").strip()
        if len(content) > MESSAGE_MAX_CHARS:
            content = content[:MESSAGE_MAX_CHARS] + "……"
        lines.append(f"{speaker}：{content}")
    return "\n".join(lines)


def _summarize(old_summary: str, messages: list[dict], endpoint: ModelEndpoint) -> str:
    user_content = f"【旧摘要】\n{old_summary.strip() or '（无）'}\n\n【新的对话】\n{_transcript(messages)}"
    response = client_for(endpoint, timeout=SUMMARY_TIMEOUT_SECONDS, retries=1).chat.completions.create(
        model=endpoint.model,
        messages=[
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=SUMMARY_TEMPERATURE,
        max_tokens=SUMMARY_MAX_TOKENS,
    )
    return (response.choices[0].message.content or "").strip()[:SUMMARY_MAX_CHARS * 2]


def refresh(tenant_id: str, user_id: str, thread_id: str, endpoint: ModelEndpoint) -> bool:
    """同步执行一次：够批次就重写摘要，返回是否写入了新摘要。"""
    try:
        job = chat_history.summary_job(tenant_id, user_id, thread_id, KEEP_TURNS, SUMMARY_BATCH_TURNS)
        if job is None or not job.messages:
            return False
        text = _summarize(job.summary, job.messages, endpoint)
        if not text:
            return False
        saved = chat_history.save_summary(tenant_id, user_id, thread_id, job, text)
        if saved:
            logger.info(f"[摘要] 会话 {thread_id} 已压缩到第 {job.upto_seq} 条")
        return saved
    except Exception as exc:
        logger.warning(f"[摘要] 会话 {thread_id} 压缩失败，保留旧摘要: {scrub(str(exc), endpoint)}")
        return False


def schedule(tenant_id: str, user_id: str, thread_id: str, endpoint: ModelEndpoint | None) -> None:
    """在后台线程里跑 refresh；没有可用模型时什么都不做。"""
    if endpoint is None:
        return
    _executor.submit(refresh, tenant_id, user_id, thread_id, endpoint)
