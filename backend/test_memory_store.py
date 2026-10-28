"""长期记忆存储回归测试。用进程内的假 Postgres + 假 Redis + 假向量模型，不需要真实服务:
    cd backend && pytest test_memory_store.py
"""
from __future__ import annotations

import json

import memory_store
import pytest


class FakeCursor:
    """把 memory_store 用到的那几条 SQL，在内存里实现出来。"""

    def __init__(self, db: "FakeDb") -> None:
        self.db = db
        self.rows: list = []
        self.rowcount = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        compact = " ".join(sql.split())
        store = self.db.notes

        if compact.startswith("INSERT INTO user_memories"):
            (mid, tenant, user, content, kind, slot, origin, pinned, sensitive,
             embedding, created, updated) = params
            if mid in store:
                self.rowcount = 0
                return
            store[mid] = {
                "id": mid, "tenantId": tenant, "userId": user, "content": content,
                "kind": kind, "slot": slot, "origin": origin, "pinned": pinned,
                "sensitive": sensitive, "embedding": list(embedding),
                "created_at": created, "updated_at": updated,
            }
            self.rowcount = 1
        elif compact.startswith("UPDATE user_memories SET content"):
            if "pinned = %s" in compact:
                (content, kind, slot, origin, pinned, sensitive, embedding,
                 updated, mid, tenant, user) = params
            else:
                (content, kind, slot, origin, sensitive,
                 embedding, updated, mid, tenant, user) = params
                pinned = store.get(mid, {}).get("pinned", False)
            row = store.get(mid)
            if row is None or row["tenantId"] != tenant or row["userId"] != user:
                self.rowcount = 0
                return
            row.update(
                content=content, kind=kind, slot=slot, origin=origin, pinned=pinned,
                sensitive=sensitive, embedding=list(embedding), updated_at=updated,
            )
            self.rowcount = 1
        elif compact.startswith("UPDATE user_memories SET kind"):
            kind, slot, sensitive, mid, tenant, user = params
            row = store.get(mid)
            if row is not None and row["tenantId"] == tenant and row["userId"] == user:
                row.update(kind=kind, slot=slot, sensitive=sensitive)
        elif compact.startswith("DELETE FROM user_memories WHERE id IN"):
            tenant, user, need = params
            mine = [row for row in store.values()
                    if row["tenantId"] == tenant and row["userId"] == user]
            mine.sort(key=lambda row: (int(row["pinned"]), int(bool(row["slot"])), row["updated_at"]))
            for row in mine[:need]:
                store.pop(row["id"], None)
        elif compact.startswith("DELETE FROM user_memories WHERE id"):
            mid, tenant, user = params
            row = store.get(mid)
            self.rowcount = int(
                row is not None and row["tenantId"] == tenant and row["userId"] == user
            )
            store.pop(mid, None)
        elif compact.startswith('DELETE FROM user_memories WHERE "tenantId" = %s AND "userId" = %s AND id = ANY'):
            tenant, user, ids = params
            for mid in ids:
                store.pop(mid, None)
        elif compact.startswith('DELETE FROM user_memories WHERE "tenantId"'):
            tenant, user = params
            ids = [mid for mid, row in store.items()
                   if row["tenantId"] == tenant and row["userId"] == user]
            for mid in ids:
                store.pop(mid)
            self.rowcount = len(ids)
        elif "SELECT COUNT(*) FROM user_memories" in compact:
            tenant, user = params
            self.rows = [(sum(1 for row in store.values()
                              if row["tenantId"] == tenant and row["userId"] == user),)]
        elif compact.startswith("SELECT id, content"):
            tenant, user = params
            rows = [
                (row["id"], row["content"], row["kind"], row["slot"], row["origin"],
                 row["pinned"], row["sensitive"], row["embedding"],
                 row["created_at"], row["updated_at"])
                for row in store.values()
                if row["tenantId"] == tenant and row["userId"] == user
            ]
            # 真实 SQL 里是 ORDER BY "createdAt" DESC
            self.rows = sorted(rows, key=lambda item: item[8], reverse=True)
        elif compact.startswith("DELETE FROM user_memories WHERE id IN"):
            tenant, user, need = params
            mine = [row for row in store.values()
                    if row["tenantId"] == tenant and row["userId"] == user]
            mine.sort(key=lambda row: (int(row["pinned"]), int(bool(row["slot"])), row["updated_at"]))
            for row in mine[:need]:
                store.pop(row["id"], None)
        elif compact.startswith("SELECT 1"):
            self.rows = []

    def fetchall(self) -> list:
        return self.rows

    def fetchone(self) -> tuple | None:
        return self.rows[0] if self.rows else None


class FakeDb:
    """假连接：`with connect() as conn` 与 `conn.cursor()` 都只要上下文协议。"""

    def __init__(self) -> None:
        self.notes: dict[str, dict] = {}

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        return None

    def __enter__(self) -> "FakeDb":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeRedis:
    """只实现缓存清理与整理频率标记用到的命令。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def delete(self, key: str) -> int:
        return int(self.data.pop(key, None) is not None)

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def hgetall(self, key: str) -> dict[str, str]:
        raw = self.data.get(key)
        if raw is None or not raw.startswith("{"):
            return {}
        return json.loads(raw)


class FakeEmbedder:
    """把文本映射成可预测的向量：同一批字共享的分量越接近，余弦越高。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def _vector(self, text: str) -> list[float]:
        # 24 维、按字散列；「奖学金」这类同词文本余弦自然高于无关文本
        axes = [0.0] * 24
        for char in text:
            axes[hash(char) % 24] += 1.0
        return axes

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
    monkeypatch.setattr(memory_store, "_connect", lambda: fake)
    # 连续写入可能落在同一个时钟刻度上，排序就不确定了
    ticks = iter(range(1_000_000, 2_000_000))
    monkeypatch.setattr(memory_store.time, "time", lambda: float(next(ticks)))
    monkeypatch.setattr(memory_store, "_redis", lambda: FakeRedis())
    monkeypatch.setattr(memory_store, "_cache", {})
    return fake


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> FakeEmbedder:
    fake = FakeEmbedder()
    monkeypatch.setattr(memory_store, "_embedder_factory", lambda tenant_id: fake)
    return fake


def _contents(tenant_id: str, user_id: str) -> list[str]:
    return [note.content for note in memory_store.list_memories(tenant_id, user_id)]


def _without_cache() -> None:
    memory_store._cache.clear()


def test_add_dedupes_and_lists_newest_first(db: FakeDb) -> None:
    memory_store.add_memories("t1", "u1", ["我是计算机学院的"])
    added = memory_store.add_memories("t1", "u1", ["我是计算机学院的", "我读大三"])

    assert [note.content for note in added] == ["我读大三"]
    notes = memory_store.list_memories("t1", "u1")
    assert [note.content for note in notes] == ["我读大三", "我是计算机学院的"]


def test_users_are_isolated(db: FakeDb) -> None:
    """同校不同人、同 id 不同校，都不能读到对方的记忆。"""
    memory_store.add_memories("t1", "u1", ["甲的秘密"])

    assert memory_store.list_memories("t1", "u2") == []
    assert memory_store.list_memories("t2", "u1") == []


def test_disabled_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """没配 DATABASE_URL 时记忆整体降级为不记，不能抛异常拖垮问答。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert memory_store.enabled() is False
    assert memory_store.add_memories("t1", "u1", ["我读大三"]) == []
    assert memory_store.list_memories("t1", "u1") == []


def test_capacity_drops_oldest(db: FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_store, "MAX_MEMORIES", 3)
    memory_store.add_memories("t1", "u1", ["一", "二"])
    memory_store.add_memories("t1", "u1", ["三", "四"])

    assert set(_contents("t1", "u1")) == {"二", "三", "四"}


def test_capacity_keeps_pinned_and_profile(db: FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    """满了先丢无关的老条目，置顶的和个人档案要留住。"""
    monkeypatch.setattr(memory_store, "MAX_MEMORIES", 3)
    memory_store.add_memories("t1", "u1", ["我读大三", "我喜欢打篮球"])
    pinned = memory_store.add_memories("t1", "u1", ["我姓唐"])[0]
    memory_store.update_memory("t1", "u1", pinned.id, pinned=True)

    memory_store.add_memories("t1", "u1", ["最近在准备四级"])

    kept = set(_contents("t1", "u1"))
    assert "我姓唐" in kept
    assert "我读大三" in kept
    assert "我喜欢打篮球" not in kept


def test_content_is_trimmed_and_capped(db: FakeDb) -> None:
    long_text = "很长" * 300
    added = memory_store.add_memories("t1", "u1", ["  有   空格  ", long_text, "   "])

    assert added[0].content == "有 空格"
    assert len(added[1].content) == memory_store.MAX_MEMORY_CHARS
    assert len(added) == 2


def test_same_slot_is_overwritten_not_duplicated(db: FakeDb) -> None:
    """年级变了就说一句，旧的要消失，不能两条一起进提示词。"""
    memory_store.add_memories("t1", "u1", ["我读大三"])
    result = memory_store.remember("t1", "u1", ["我今年大四了"])

    assert [note.content for note in result.updated] == ["我今年大四了"]
    assert result.added == []
    assert _contents("t1", "u1") == ["我今年大四了"]


def test_slot_is_inferred_or_taken_from_structured_fact(db: FakeDb) -> None:
    assert memory_store.infer_slot("我是计算机学院的学生") == "college"
    assert memory_store.infer_slot("我读大二") == "grade"
    assert memory_store.infer_slot("我喜欢打篮球") == ""

    memory_store.remember("t1", "u1", [{"content": "软件工程", "slot": "major"}])
    assert memory_store.list_memories("t1", "u1")[0].slot == "major"


@pytest.mark.parametrize(
    "content",
    [
        "我的手机号是13800138000",
        "我的身份证号是110101",
        "我确诊了抑郁症",
        "我家里低保",
        "我的密码是abc123",
    ],
)
def test_auto_extraction_refuses_private_facts(db: FakeDb, content: str) -> None:
    result = memory_store.remember("t1", "u1", [content], origin="auto")

    assert result.rejected == [content]
    assert memory_store.list_memories("t1", "u1") == []


def test_manual_remember_keeps_private_fact_but_flags_it(db: FakeDb) -> None:
    """学生自己要求记的照存，但标成隐私，之后不会自动带进回答。"""
    note = memory_store.add_memories("t1", "u1", ["我确诊了抑郁症，别忘了我对花生过敏"])[0]

    assert note.sensitive is True
    assert memory_store.relevant_memories("t1", "u1", "过敏") == []


def test_ordinary_facts_are_not_flagged_sensitive(db: FakeDb) -> None:
    note = memory_store.add_memories("t1", "u1", ["我读计算机学院大二"])[0]

    assert note.sensitive is False
    assert memory_store.relevant_memories("t1", "u1", "计算机") == ["我读计算机学院大二"]


def test_delete(db: FakeDb) -> None:
    note = memory_store.add_memories("t1", "u1", ["要删的"])[0]

    assert memory_store.delete_memory("t1", "u1", note.id) is True
    assert memory_store.delete_memory("t1", "u1", note.id) is False
    assert memory_store.list_memories("t1", "u1") == []


def test_clear_removes_everything(db: FakeDb) -> None:
    memory_store.add_memories("t1", "u1", ["一", "二"])

    assert memory_store.clear_memories("t1", "u1") == 2
    assert memory_store.list_memories("t1", "u1") == []


def test_update_can_edit_and_pin(db: FakeDb) -> None:
    note = memory_store.add_memories("t1", "u1", ["我读大二"])[0]

    edited = memory_store.update_memory("t1", "u1", note.id, content="我读大三")
    assert edited is not None and edited.content == "我读大三"

    pinned = memory_store.update_memory("t1", "u1", note.id, pinned=True)
    assert pinned is not None and pinned.pinned is True

    assert memory_store.update_memory("t1", "u1", "nope", content="x") is None


def test_editing_cannot_unflag_a_private_fact(db: FakeDb) -> None:
    note = memory_store.add_memories("t1", "u1", ["我确诊了抑郁症"])[0]

    edited = memory_store.update_memory("t1", "u1", note.id, content="我在准备考研")

    assert edited is not None
    assert edited.sensitive is True


def test_relevant_memories_prefers_overlap(db: FakeDb) -> None:
    """中文按两字切词：问「奖学金」时应先拿到与奖学金相关的那条。"""
    memory_store.add_memories("t1", "u1", ["我拿过国家奖学金", "我住在三号宿舍", "我喜欢打篮球"])

    picked = memory_store.relevant_memories("t1", "u1", "今年奖学金怎么评", limit=1)
    assert picked == ["我拿过国家奖学金"]


def test_relevant_memories_always_includes_profile(db: FakeDb) -> None:
    """核心画像（年级/学院）必须一直在，哪怕这个问题一个字都不重合。"""
    memory_store.add_memories("t1", "u1", ["我读大三", "我是计算机学院的"])
    memory_store.add_memories("t1", "u1", ["我喜欢打篮球"])

    picked = memory_store.relevant_memories("t1", "u1", "食堂几点关", limit=3)

    assert "我读大三" in picked
    assert "我是计算机学院的" in picked


def test_relevant_memories_falls_back_to_recent(db: FakeDb) -> None:
    memory_store.add_memories("t1", "u1", ["旧的"])
    memory_store.add_memories("t1", "u1", ["新的"])

    assert memory_store.relevant_memories("t1", "u1", "完全无关", limit=1) == ["新的"]


# ---------- 向量检索 ----------


def test_vectors_are_stored_when_embedder_is_configured(db: FakeDb, embedder: FakeEmbedder) -> None:
    note = memory_store.add_memories("t1", "u1", ["我拿过国家奖学金"])[0]

    assert note.embedding
    assert embedder.calls == [["我拿过国家奖学金"]]


def test_private_facts_are_not_embedded(db: FakeDb, embedder: FakeEmbedder) -> None:
    """隐私条目本来就不进提示词，没必要为它花一次 embedding 调用。"""
    note = memory_store.add_memories("t1", "u1", ["我确诊了抑郁症"])[0]

    assert note.sensitive is True
    assert note.embedding == ()
    assert embedder.calls == []


def test_vector_similarity_picks_semantically_close_note(db: FakeDb, embedder: FakeEmbedder) -> None:
    """「奖学金」和「评优评先」共字不多，向量能把它捞上来。"""
    memory_store.add_memories("t1", "u1", ["我拿过国家奖学金", "我住在三号宿舍"])
    memory_store.add_memories("t1", "u1", ["我在准备四级"])

    picked = memory_store.relevant_memories("t1", "u1", "奖学金的评定", limit=2)

    assert picked[0] == "我拿过国家奖学金"


def test_search_survives_embedding_failure(db: FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    """智谱挂了不能让学生问不了话：退回词匹配照样能挑出相关记忆。"""

    class Broken:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("余额不足")

        def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("余额不足")

    monkeypatch.setattr(memory_store, "_embedder_factory", lambda tenant_id: Broken())
    memory_store.add_memories("t1", "u1", ["我拿过国家奖学金", "我住在三号宿舍"])

    picked = memory_store.relevant_memories("t1", "u1", "奖学金怎么评", limit=1)

    assert picked == ["我拿过国家奖学金"]


# ---------- 整理 ----------


def _legacy_row(mid: str, content: str, created_at: float) -> dict:
    """升级前的条目：没有 kind / slot / sensitive 判断。"""
    return {
        "id": mid, "tenantId": "t1", "userId": "u1", "content": content,
        "kind": "fact", "slot": "", "origin": "auto", "pinned": False,
        "sensitive": False, "embedding": [], "created_at": created_at,
        "updated_at": created_at,
    }


def test_consolidate_merges_duplicate_grades(db: FakeDb) -> None:
    """升级前的老数据没有槽位，可能同时躺着「大三」「大四」，整理后只留最新的。"""
    db.notes.update({
        "old": _legacy_row("old", "我读大三", 100.0),
        "new": _legacy_row("new", "我已经大四了", 200.0),
        "other": _legacy_row("other", "我喜欢打篮球", 150.0),
    })

    changed = memory_store.consolidate_memories("t1", "u1")

    assert changed >= 1
    contents = _contents("t1", "u1")
    assert "我读大三" not in contents
    assert "我已经大四了" in contents
    assert "我喜欢打篮球" in contents


def test_consolidate_backfills_privacy_flag(db: FakeDb) -> None:
    """升级前的条目没做过隐私判断，整理时要补标。"""
    db.notes["m1"] = _legacy_row("m1", "我确诊了抑郁症", 100.0)

    memory_store.consolidate_memories("t1", "u1")

    assert memory_store.list_memories("t1", "u1")[0].sensitive is True


def test_maybe_consolidate_runs_once_a_day(db: FakeDb) -> None:
    fake_redis = FakeRedis()
    memory_store._redis = lambda: fake_redis  # type: ignore[assignment]
    memory_store.add_memories("t1", "u1", ["我读大三"])
    memory_store.add_memories("t1", "u1", ["我已经大四了"])

    assert memory_store.maybe_consolidate("t1", "u1") >= 0
    # 第二次被频率标记挡住，不会再干活
    assert memory_store.maybe_consolidate("t1", "u1") == 0


# ---------- 旧数据迁移 ----------


def test_legacy_redis_memories_are_migrated(db: FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = {
        "m1": json.dumps({"id": "m1", "content": "我读大三", "created_at": 1.0, "origin": "auto"}),
        "m2": json.dumps({"id": "m2", "content": "我喜欢打篮球", "created_at": 2.0, "origin": "auto"}),
    }
    fake_redis = FakeRedis()
    fake_redis.data["memory:t1:u1"] = json.dumps(legacy)
    monkeypatch.setattr(memory_store, "_redis", lambda: fake_redis)

    migrated = memory_store.migrate_from_redis("t1", "u1")

    assert migrated == 2
    assert set(_contents("t1", "u1")) == {"我读大三", "我喜欢打篮球"}
    # 搬完就删掉，别每次读都重搬
    assert "memory:t1:u1" not in fake_redis.data


def test_migration_is_skipped_when_nothing_legacy(db: FakeDb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_store, "_redis", lambda: FakeRedis())

    assert memory_store.migrate_from_redis("t1", "u1") == 0
    assert memory_store.list_memories("t1", "u1") == []


# ---------- 缓存 ----------


def test_list_is_cached_until_invalidated(db: FakeDb) -> None:
    memory_store.add_memories("t1", "u1", ["我读大三"])
    first = memory_store.list_memories("t1", "u1")

    # 缓存期内直接改库，读到的仍是缓存
    next(iter(db.notes.values()))["content"] = "偷偷改掉"
    assert memory_store.list_memories("t1", "u1")[0].content == first[0].content

    # 写入会清缓存，下一读就是新的
    memory_store.add_memories("t1", "u1", ["我喜欢打篮球"])
    assert "偷偷改掉" in _contents("t1", "u1")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("记住：我是计算机学院的", "我是计算机学院的"),
        ("记住:  我读大三 ", "我读大三"),
        ("请记住这件事", None),
        ("记住：   ", None),
    ],
)
def test_remember_command(text: str, expected: str | None) -> None:
    assert memory_store.take_remember_command(text) == expected
