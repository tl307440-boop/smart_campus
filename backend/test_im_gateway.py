"""企微长连接 / QQ OneBot v11 / IM 统一网关单元测试。"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import im_gateway
import pytest
import qq_bot
import wecom_bot


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def exists(self, key: str) -> int:
        return 1 if key in self.kv else 0

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.kv[key] = value
        return True

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self.kv:
                del self.kv[k]
                n += 1
        return n


class FakeBindingDb:
    """内存版 im_bindings 存储。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.seq = 0

    def connect(self):
        db = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def cursor(self):
                return BindingCursor(db)

        return Conn()


class BindingCursor:
    def __init__(self, db: FakeBindingDb) -> None:
        self.db = db
        self._rows: list[tuple] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def _to_tuple(self, item: dict) -> tuple:
        return (
            item["id"],
            item["tenantId"],
            item["userId"],
            item["platform"],
            item["externalId"],
            item["displayName"],
            item["morningDigest"],
            item["digestTime"],
            item["gradeAlert"],
            item["ticketAlert"],
            item["newsKeywords"],
            item["lastGradeHash"],
            item["createdAt"],
            item["updatedAt"],
        )

    def execute(self, sql: str, params: tuple = ()) -> None:
        compact = " ".join(sql.split())
        self.db.seq += 1
        now = datetime(2026, 11, 13, 8, 0, self.db.seq % 60, tzinfo=timezone.utc)

        if compact.startswith("INSERT INTO im_bindings"):
            bid, tenant_id, user_id, platform, ext_id, display_name = params
            existing = next(
                (
                    r
                    for r in self.db.rows.values()
                    if r["tenantId"] == tenant_id and r["platform"] == platform and r["externalId"] == ext_id
                ),
                None,
            )
            if existing:
                existing["userId"] = user_id
                if display_name:
                    existing["displayName"] = display_name
                existing["updatedAt"] = now
                self._rows = [self._to_tuple(existing)]
                self.rowcount = 1
                return
            rec = {
                "id": bid,
                "tenantId": tenant_id,
                "userId": user_id,
                "platform": platform,
                "externalId": ext_id,
                "displayName": display_name,
                "morningDigest": True,
                "digestTime": "07:30",
                "gradeAlert": True,
                "ticketAlert": True,
                "newsKeywords": "",
                "lastGradeHash": "",
                "createdAt": now,
                "updatedAt": now,
            }
            self.db.rows[bid] = rec
            self._rows = [self._to_tuple(rec)]
            self.rowcount = 1
            return

        if 'FROM im_bindings WHERE "platform" = %s AND "externalId" = %s AND "tenantId" = %s' in compact:
            platform, ext_id, tenant_id = params
            matched = [
                r
                for r in self.db.rows.values()
                if r["platform"] == platform and r["externalId"] == ext_id and r["tenantId"] == tenant_id
            ]
            self._rows = [self._to_tuple(matched[0])] if matched else []
            return

        if 'FROM im_bindings WHERE "platform" = %s AND "externalId" = %s ORDER BY' in compact:
            platform, ext_id = params
            matched = [r for r in self.db.rows.values() if r["platform"] == platform and r["externalId"] == ext_id]
            matched.sort(key=lambda r: r["updatedAt"], reverse=True)
            self._rows = [self._to_tuple(matched[0])] if matched else []
            return

        if 'FROM im_bindings WHERE "tenantId" = %s AND "userId" = %s' in compact:
            tenant_id, user_id = params
            matched = [r for r in self.db.rows.values() if r["tenantId"] == tenant_id and r["userId"] == user_id]
            matched.sort(key=lambda r: r["createdAt"])
            self._rows = [self._to_tuple(r) for r in matched]
            return

        if compact.startswith("UPDATE im_bindings SET"):
            bid, tenant_id, user_id = params[-3:]
            item = self.db.rows.get(bid)
            if not item or item["tenantId"] != tenant_id or item["userId"] != user_id:
                self._rows = []
                self.rowcount = 0
                return
            # 按 SET 字段顺序回写
            vals = list(params[:-3])
            idx = 0
            for field in ('"morningDigest" = %s', '"digestTime" = %s', '"gradeAlert" = %s', '"ticketAlert" = %s', '"newsKeywords" = %s'):
                if field in compact:
                    key = field.split('"')[1]
                    item[key] = vals[idx]
                    idx += 1
            item["updatedAt"] = now
            self._rows = [self._to_tuple(item)]
            self.rowcount = 1
            return

        if compact.startswith('DELETE FROM im_bindings WHERE "tenantId" = %s AND "platform" = %s AND "externalId" = %s'):
            tenant_id, platform, ext_id = params
            victim = [
                k
                for k, v in self.db.rows.items()
                if v["tenantId"] == tenant_id and v["platform"] == platform and v["externalId"] == ext_id
            ]
            for k in victim:
                del self.db.rows[k]
            self.rowcount = len(victim)
            return

        if compact.startswith('DELETE FROM im_bindings WHERE "id" = %s AND "tenantId" = %s AND "userId" = %s'):
            bid, tenant_id, user_id = params
            item = self.db.rows.get(bid)
            if item and item["tenantId"] == tenant_id and item["userId"] == user_id:
                del self.db.rows[bid]
                self.rowcount = 1
            else:
                self.rowcount = 0
            return

        raise AssertionError(f"Unexpected SQL: {compact}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@pytest.fixture
def im_env(monkeypatch: pytest.MonkeyPatch):
    fake_redis = FakeRedis()
    fake_db = FakeBindingDb()
    monkeypatch.setattr(im_gateway, "_redis", lambda: fake_redis)
    monkeypatch.setattr(im_gateway, "_connect", fake_db.connect)
    monkeypatch.setattr(im_gateway.chat_history, "enabled", lambda: True)
    return fake_redis, fake_db


def test_bind_code_lifecycle_preferences_and_commands(im_env, monkeypatch: pytest.MonkeyPatch) -> None:
    # 未绑定时普通问题触发绑定引导
    guide = im_gateway.handle_incoming_message("qq", "10001", "明天有什么课")
    assert "还没有绑定校园账号" in guide
    assert "绑定 123456" in guide

    # 生成 6 位绑定码并完成绑定
    code = im_gateway.create_bind_code("t1", "u1")
    assert len(code) == 6 and code.isdigit()

    bound_msg = im_gateway.handle_incoming_message("qq", "10001", f"绑定 {code}", display_name="小明")
    assert "绑定成功" in bound_msg and "`t1`" in bound_msg

    # 绑定码一次性消费，重放无效
    replay = im_gateway.handle_incoming_message("qq", "10002", f"绑定 {code}")
    assert "绑定失败" in replay

    bindings = im_gateway.list_user_bindings("t1", "u1")
    assert len(bindings) == 1
    bid = bindings[0]["id"]

    # 更新推送偏好及非法时间校验
    with pytest.raises(ValueError, match="HH:MM"):
        im_gateway.update_binding_preferences("t1", "u1", bid, {"digest_time": "25:99"})
    updated = im_gateway.update_binding_preferences(
        "t1", "u1", bid, {"digest_time": "08:15", "news_keywords": "奖学金 选课"}
    )
    assert updated is not None
    assert updated["digest_time"] == "08:15" and updated["news_keywords"] == "奖学金 选课"

    # 快捷指令路由：帮助 / 早报 / 自然语言问答 / 解绑
    assert "快捷指令" in im_gateway.handle_incoming_message("qq", "10001", "帮助")

    import im_scheduler

    monkeypatch.setattr(im_scheduler, "build_morning_digest", lambda t, u, day, kw="": f"DIGEST:{t}:{u}:{kw}")
    assert im_gateway.handle_incoming_message("qq", "10001", "早报") == "DIGEST:t1:u1:奖学金 选课"

    monkeypatch.setattr(
        im_gateway,
        "_ask_campus_graph",
        lambda t, u, tid, q: f"ANS({t}/{u}):{q}",
    )
    assert im_gateway.handle_incoming_message("qq", "10001", "图书馆几点关门") == "ANS(t1/u1):图书馆几点关门"

    assert "已解除当前账号绑定" in im_gateway.handle_incoming_message("qq", "10001", "解绑")
    assert im_gateway.list_user_bindings("t1", "u1") == []


def test_format_im_answer_tables_and_sources() -> None:
    rows = [{"course": f"课程{i}", "score": str(80 + i)} for i in range(18)]
    formatted = im_gateway.format_im_answer(
        "以下是你的成绩查询结果：",
        tables=[{"title": "期末成绩", "total": 18, "columns": [{"key": "course", "label": "课程"}, {"key": "score", "label": "成绩"}], "rows": rows}],
        sources=[{"source": "教务处成绩管理办法.pdf", "url": "https://jw.example.edu/rules"}],
    )
    assert "【期末成绩】" in formatted
    assert "课程: 课程0 · 成绩: 80" in formatted
    assert "共 18 条" in formatted  # 超过 15 行安全截断提示
    assert "[教务处成绩管理办法.pdf](https://jw.example.edu/rules)" in formatted


def test_qq_onebot_signature_and_event_parsing() -> None:
    body = json.dumps({"post_type": "message", "user_id": 10001}).encode("utf-8")
    sig = hmac.new(b"secret-123", body, hashlib.sha1).hexdigest()
    assert qq_bot.verify_signature(body, f"sha1={sig}", "secret-123") is True
    assert qq_bot.verify_signature(body, "sha1=deadbeef", "secret-123") is False
    assert qq_bot.verify_signature(body, "", "secret-123", bearer_token="Bearer secret-123") is True
    assert qq_bot.verify_signature(body, "", "") is True  # 未配 token 时放行

    # 群里未 @ 机器人的闲聊应忽略，避免刷屏
    assert qq_bot.parse_onebot_event({
        "post_type": "message",
        "message_type": "group",
        "self_id": 99999,
        "user_id": 10001,
        "group_id": 888,
        "message": "大家中午吃什么",
    }) is None

    # 群里通过消息段数组 @ 机器人应当响应
    parsed_group = qq_bot.parse_onebot_event({
        "post_type": "message",
        "message_type": "group",
        "self_id": 99999,
        "user_id": 10001,
        "group_id": 888,
        "sender": {"card": "计算机2301-张三"},
        "message": [
            {"type": "at", "data": {"qq": "99999"}},
            {"type": "text", "data": {"text": "  明天高数在哪上  "}},
        ],
    })
    assert parsed_group == {
        "external_id": "10001",
        "display_name": "计算机2301-张三",
        "is_group": True,
        "group_id": "888",
        "text": "明天高数在哪上",
    }

    # 群聊以指令词或唤醒前缀开头也应响应
    wake = qq_bot.parse_onebot_event({
        "post_type": "message",
        "message_type": "group",
        "self_id": 99999,
        "user_id": 10002,
        "group_id": 888,
        "message": "小懒，查一下今天课表",
    })
    assert wake is not None and wake["text"] == "查一下今天课表"

    # 机器人自身回声消息忽略
    assert qq_bot.parse_onebot_event({
        "post_type": "message",
        "message_type": "private",
        "self_id": 99999,
        "user_id": 99999,
        "message": "你好",
    }) is None


def test_wecom_callback_parsing_and_process_frame(im_env, monkeypatch: pytest.MonkeyPatch) -> None:
    sub = wecom_bot.build_subscribe_frame("bot-1", "sec-1")
    assert sub["cmd"] == "aibot_subscribe"
    assert sub["body"] == {"bot_id": "bot-1", "secret": "sec-1"}
    assert sub["headers"]["req_id"]

    # 忽略心跳 / 空帧
    assert wecom_bot.parse_wecom_callback({"cmd": "ping"}) is None

    # 混合消息过滤并剥离前置 @机器人
    frame = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-42"},
        "body": {
            "msgtype": "mixed",
            "from": {"userid": "zhangsan", "name": "张三"},
            "chatid": "room-1",
            "mixed": {
                "msg_item": [
                    {"msgtype": "text", "text": {"content": "@校园助手 帮助"}},
                    {"msgtype": "image", "image": {"url": "https://example.com/1.png"}},
                ]
            },
        },
    }
    parsed = wecom_bot.parse_wecom_callback(frame)
    assert parsed == {
        "req_id": "req-42",
        "external_id": "zhangsan",
        "chat_id": "room-1",
        "display_name": "张三",
        "text": "帮助",
    }

    resp = wecom_bot.process_callback_frame("t1", frame)
    assert resp is not None
    assert resp["cmd"] == "aibot_respond_msg"
    assert resp["headers"] == {"req_id": "req-42"}
    assert "快捷指令" in resp["body"]["markdown"]["content"]
