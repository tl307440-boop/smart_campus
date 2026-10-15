"""滚动摘要：够批次才调模型、写入带乐观锁、失败保留旧摘要。不连库不连模型。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import session_summary
from ai_config import ModelEndpoint
from chat_history import SummaryJob

ENDPOINT = ModelEndpoint("sk-test", "https://api.example.com/v1", "cheap-model", "school")
JOB = SummaryJob(
    summary="学生读大三",
    from_seq=0,
    upto_seq=10,
    messages=[{"role": "user", "content": "我在找空教室"}, {"role": "assistant", "content": "三教 105 空着"}],
)


def _model(monkeypatch: pytest.MonkeyPatch, reply: str = "- 学生读大三\n- 在找空教室") -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content=reply))]
    monkeypatch.setattr(session_summary, "client_for", lambda *args, **kwargs: client)
    return client


def test_writes_merged_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _model(monkeypatch)
    monkeypatch.setattr(session_summary.chat_history, "summary_job", lambda *args: JOB)
    saver = MagicMock(return_value=True)
    monkeypatch.setattr(session_summary.chat_history, "save_summary", saver)

    assert session_summary.refresh("t", "u", "s1", ENDPOINT) is True

    prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "学生读大三" in prompt and "学生：我在找空教室" in prompt and "助手：三教 105 空着" in prompt
    saver.assert_called_once_with("t", "u", "s1", JOB, "- 学生读大三\n- 在找空教室")


def test_skips_model_until_batch_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _model(monkeypatch)
    calls: list[tuple] = []
    monkeypatch.setattr(session_summary.chat_history, "summary_job", lambda *args: calls.append(args))

    assert session_summary.refresh("t", "u", "s1", ENDPOINT) is False
    assert calls == [("t", "u", "s1", session_summary.KEEP_TURNS, session_summary.SUMMARY_BATCH_TURNS)]
    client.chat.completions.create.assert_not_called()


def test_model_failure_keeps_old_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _model(monkeypatch)
    client.chat.completions.create.side_effect = RuntimeError("sk-test 余额不足")
    monkeypatch.setattr(session_summary.chat_history, "summary_job", lambda *args: JOB)
    saver = MagicMock()
    monkeypatch.setattr(session_summary.chat_history, "save_summary", saver)

    assert session_summary.refresh("t", "u", "s1", ENDPOINT) is False
    saver.assert_not_called()


def test_long_messages_are_clipped() -> None:
    text = session_summary._transcript([{"role": "user", "content": "长" * (session_summary.MESSAGE_MAX_CHARS + 50)}])
    assert len(text) < session_summary.MESSAGE_MAX_CHARS + 10


def test_window_and_summary_overlap() -> None:
    # 摘要之外未压缩的部分最多 KEEP + BATCH - 1 轮，要能装进提示词窗口才不会漏轮
    assert session_summary.KEEP_TURNS + session_summary.SUMMARY_BATCH_TURNS <= session_summary.MAX_PROMPT_TURNS


def test_no_endpoint_no_job(monkeypatch: pytest.MonkeyPatch) -> None:
    submit = MagicMock()
    monkeypatch.setattr(session_summary._executor, "submit", submit)
    session_summary.schedule("t", "u", "s1", None)
    submit.assert_not_called()
