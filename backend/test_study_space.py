"""个人课程伴学空间单元测试（.pptx 解析、私有分块检索、SM-2 闪卡、出题批改与工具隔离）。"""
from __future__ import annotations

import io
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import agent
import file_service
import jw_account
import pytest
import study_space
from pptx import Presentation
from workspace import WorkspaceSettings


class FakeStudyDb:
    """内存版个人伴学 Postgres 表，实现严格按 (tenantId, userId) 隔离。"""

    def __init__(self) -> None:
        self.courses: dict[str, dict] = {}
        self.materials: dict[str, dict] = {}
        self.chunks: dict[str, dict] = {}
        self.cards: dict[str, dict] = {}
        self.seq = 0

    def connect(self):
        db = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def cursor(self):
                return StudyCursor(db)

        return Conn()


class StudyCursor:
    def __init__(self, db: FakeStudyDb) -> None:
        self.db = db
        self._rows: list[tuple] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def _card_tuple(self, c: dict) -> tuple:
        return (
            c["id"],
            c["courseId"],
            c["materialId"],
            c["front"],
            c["back"],
            c["tags"],
            c["reps"],
            c["intervalDays"],
            c["ease"],
            c["dueAt"],
            c["lastReviewedAt"],
            c["createdAt"],
        )

    def execute(self, sql: str, params: tuple = ()) -> None:
        compact = " ".join(sql.split())
        self.db.seq += 1
        now = datetime(2026, 11, 13, 10, 0, self.db.seq % 60, tzinfo=timezone.utc)

        if compact.startswith("INSERT INTO study_courses"):
            cid, tenant_id, user_id, name, code, teacher, term, source = params
            existing = next(
                (
                    r
                    for r in self.db.courses.values()
                    if r["tenantId"] == tenant_id and r["userId"] == user_id and r["name"] == name
                ),
                None,
            )
            if existing:
                if teacher:
                    existing["teacher"] = teacher
                if term:
                    existing["term"] = term
                if code:
                    existing["code"] = code
                existing["updatedAt"] = now
                r = existing
            else:
                r = {
                    "id": cid,
                    "tenantId": tenant_id,
                    "userId": user_id,
                    "name": name,
                    "code": code,
                    "teacher": teacher,
                    "term": term,
                    "source": source,
                    "createdAt": now,
                    "updatedAt": now,
                }
                self.db.courses[cid] = r
            self._rows = [
                (r["id"], r["name"], r["code"], r["teacher"], r["term"], r["source"], r["createdAt"], r["updatedAt"])
            ]
            self.rowcount = 1
            return

        if "FROM study_courses c LEFT JOIN study_materials" in compact:
            tenant_id, user_id = params
            courses = [r for r in self.db.courses.values() if r["tenantId"] == tenant_id and r["userId"] == user_id]
            courses.sort(key=lambda r: (-r["updatedAt"].timestamp(), r["name"]))
            out = []
            real_now = datetime.now(timezone.utc)
            for c in courses:
                mats = [m for m in self.db.materials.values() if m["courseId"] == c["id"]]
                cards = [k for k in self.db.cards.values() if k["courseId"] == c["id"]]
                due_cards = [k for k in cards if k["dueAt"] <= real_now]
                out.append((
                    c["id"],
                    c["name"],
                    c["code"],
                    c["teacher"],
                    c["term"],
                    c["source"],
                    c["createdAt"],
                    c["updatedAt"],
                    len(mats),
                    sum(m["charCount"] for m in mats),
                    len(cards),
                    len(due_cards),
                ))
            self._rows = out
            return

        if compact.startswith('DELETE FROM study_courses WHERE "id" = %s'):
            cid, tenant_id, user_id = params
            item = self.db.courses.get(cid)
            if item and item["tenantId"] == tenant_id and item["userId"] == user_id:
                del self.db.courses[cid]
                for mid in [k for k, v in self.db.materials.items() if v["courseId"] == cid]:
                    del self.db.materials[mid]
                for chid in [k for k, v in self.db.chunks.items() if v["courseId"] == cid]:
                    del self.db.chunks[chid]
                for kid in [k for k, v in self.db.cards.items() if v["courseId"] == cid]:
                    del self.db.cards[kid]
                self.rowcount = 1
            else:
                self.rowcount = 0
            return

        if compact.startswith('SELECT "id", "name", "teacher" FROM study_courses'):
            cid, tenant_id, user_id = params
            item = self.db.courses.get(cid)
            if item and item["tenantId"] == tenant_id and item["userId"] == user_id:
                self._rows = [(item["id"], item["name"], item["teacher"])]
            else:
                self._rows = []
            return

        if compact.startswith("UPDATE study_courses SET"):
            cid, tenant_id, user_id = params
            item = self.db.courses.get(cid)
            if item and item["tenantId"] == tenant_id and item["userId"] == user_id:
                item["updatedAt"] = datetime.now(timezone.utc)
                self.rowcount = 1
            else:
                self.rowcount = 0
            return

        if compact.startswith("INSERT INTO study_materials"):
            mid, tenant_id, user_id, cid, title, kind, filename, content, char_count, summary = params
            self.db.materials[mid] = {
                "id": mid,
                "tenantId": tenant_id,
                "userId": user_id,
                "courseId": cid,
                "title": title,
                "kind": kind,
                "filename": filename,
                "content": content,
                "charCount": char_count,
                "summary": summary,
                "createdAt": now,
                "updatedAt": now,
            }
            self._rows = [(now,)]
            self.rowcount = 1
            return

        if compact.startswith('SELECT "id", "courseId", "title", "kind", "filename", "charCount", "summary", "createdAt" FROM study_materials'):
            tenant_id, user_id, cid = params
            mats = [
                m
                for m in self.db.materials.values()
                if m["tenantId"] == tenant_id and m["userId"] == user_id and m["courseId"] == cid
            ]
            mats.sort(key=lambda m: m["createdAt"], reverse=True)
            self._rows = [
                (m["id"], m["courseId"], m["title"], m["kind"], m["filename"], m["charCount"], m["summary"], m["createdAt"])
                for m in mats
            ]
            return

        if compact.startswith('DELETE FROM study_materials WHERE "id" = %s'):
            mid, tenant_id, user_id = params
            m = self.db.materials.get(mid)
            if m and m["tenantId"] == tenant_id and m["userId"] == user_id:
                del self.db.materials[mid]
                for chid in [k for k, v in self.db.chunks.items() if v["materialId"] == mid]:
                    del self.db.chunks[chid]
                self.rowcount = 1
            else:
                self.rowcount = 0
            return

        if compact.startswith("INSERT INTO study_chunks"):
            chid, tenant_id, user_id, cid, mid, idx, content, vec = params
            self.db.chunks[chid] = {
                "id": chid,
                "tenantId": tenant_id,
                "userId": user_id,
                "courseId": cid,
                "materialId": mid,
                "chunkIndex": idx,
                "content": content,
                "embedding": list(vec or []),
            }
            self.rowcount = 1
            return

        if 'FROM study_chunks ch JOIN study_materials' in compact:
            tenant_id = params[0]
            user_id = params[1]
            rows = []
            for ch in self.db.chunks.values():
                if ch["tenantId"] != tenant_id or ch["userId"] != user_id:
                    continue
                course = self.db.courses.get(ch["courseId"])
                mat = self.db.materials.get(ch["materialId"])
                if not course or not mat:
                    continue
                if 'AND ch."courseId" = %s' in compact and ch["courseId"] != params[2]:
                    continue
                if 'AND LOWER(c."name") LIKE %s' in compact:
                    needle = params[2].strip("%").lower()
                    if needle not in course["name"].lower():
                        continue
                rows.append((
                    ch["id"],
                    ch["courseId"],
                    course["name"],
                    ch["materialId"],
                    mat["title"],
                    ch["chunkIndex"],
                    ch["content"],
                    ch["embedding"],
                ))
            rows.sort(key=lambda r: r[5])
            self._rows = rows[:600]
            return

        if compact.startswith('SELECT "title", "content" FROM study_materials'):
            if 'WHERE "id" = %s AND "courseId" = %s' in compact:
                mid, cid, tenant_id, user_id = params
                mats = [
                    m
                    for m in self.db.materials.values()
                    if m["id"] == mid and m["courseId"] == cid and m["tenantId"] == tenant_id and m["userId"] == user_id
                ]
            else:
                cid, tenant_id, user_id = params
                mats = [
                    m
                    for m in self.db.materials.values()
                    if m["courseId"] == cid and m["tenantId"] == tenant_id and m["userId"] == user_id
                ]
                mats.sort(key=lambda m: m["updatedAt"], reverse=True)
            self._rows = [(m["title"], m["content"]) for m in mats[:8]]
            return

        if compact.startswith("INSERT INTO study_cards"):
            kid, tenant_id, user_id, cid, mid, front, back, tags = params
            rec = {
                "id": kid,
                "tenantId": tenant_id,
                "userId": user_id,
                "courseId": cid,
                "materialId": mid,
                "front": front,
                "back": back,
                "tags": tags,
                "reps": 0,
                "intervalDays": 0,
                "ease": 2.5,
                "dueAt": datetime.now(timezone.utc) - timedelta(seconds=1),
                "lastReviewedAt": None,
                "createdAt": now,
            }
            self.db.cards[kid] = rec
            self._rows = [self._card_tuple(rec)]
            self.rowcount = 1
            return

        if compact.startswith('SELECT "id", "courseId", "materialId", "front"') and "FROM study_cards" in compact:
            tenant_id, user_id, cid = params
            real_now = datetime.now(timezone.utc)
            cards = [
                k
                for k in self.db.cards.values()
                if k["tenantId"] == tenant_id and k["userId"] == user_id and k["courseId"] == cid
            ]
            if '"dueAt" <= NOW()' in compact:
                cards = [k for k in cards if k["dueAt"] <= real_now]
            cards.sort(key=lambda k: (k["dueAt"], -k["createdAt"].timestamp()))
            self._rows = [self._card_tuple(k) for k in cards[:200]]
            return

        if compact.startswith('SELECT "reps", "intervalDays", "ease" FROM study_cards'):
            kid, tenant_id, user_id = params
            k = self.db.cards.get(kid)
            if k and k["tenantId"] == tenant_id and k["userId"] == user_id:
                self._rows = [(k["reps"], k["intervalDays"], k["ease"])]
            else:
                self._rows = []
            return

        if compact.startswith("UPDATE study_cards SET"):
            next_reps, next_interval, next_ease, due_at, reviewed_at, kid, tenant_id, user_id = params
            k = self.db.cards.get(kid)
            if k and k["tenantId"] == tenant_id and k["userId"] == user_id:
                k["reps"] = next_reps
                k["intervalDays"] = next_interval
                k["ease"] = next_ease
                k["dueAt"] = due_at
                k["lastReviewedAt"] = reviewed_at
                self._rows = [self._card_tuple(k)]
                self.rowcount = 1
            else:
                self._rows = []
                self.rowcount = 0
            return

        if compact.startswith('DELETE FROM study_cards WHERE "id" = %s'):
            kid, tenant_id, user_id = params
            k = self.db.cards.get(kid)
            if k and k["tenantId"] == tenant_id and k["userId"] == user_id:
                del self.db.cards[kid]
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
def study_db(monkeypatch: pytest.MonkeyPatch) -> FakeStudyDb:
    db = FakeStudyDb()
    monkeypatch.setattr(study_space, "enabled", lambda: True)
    monkeypatch.setattr(study_space, "_connect", db.connect)
    monkeypatch.setattr(study_space, "_embed_texts", lambda tid, texts: [[] for _ in texts])
    study_space._QUIZ_CACHE.clear()
    return db


def test_pptx_extraction_with_title_table_and_notes() -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "第一章：极限与连续"
    slide.placeholders[1].text = "夹逼定理适用于不易直接求极限的数列"

    table_shape = slide.shapes.add_table(2, 2, 100000, 100000, 3000000, 1000000)
    table = table_shape.table
    table.cell(0, 0).text = "概念"
    table.cell(0, 1).text = "定义"
    table.cell(1, 0).text = "无穷小"
    table.cell(1, 1).text = "极限为零的变量"

    slide.notes_slide.notes_text_frame.text = "考研必考：两个重要极限的变形"

    buf = io.BytesIO()
    prs.save(buf)

    text = file_service.extract_text_from_bytes(buf.getvalue(), ".pptx")
    assert "第 1 页" in text
    assert "第一章：极限与连续" in text
    assert "夹逼定理" in text
    assert "无穷小 | 极限为零的变量" in text
    assert "> 讲义备注：考研必考：两个重要极限的变形" in text


def test_split_study_text_respects_boundaries() -> None:
    para = "微积分基本定理（牛顿-莱布尼茨公式）将定积分与原函数联系起来。"
    long_text = "\n\n".join(f"第{i}节：{para * 4}" for i in range(1, 10))
    chunks = study_space.split_study_text(long_text, chunk_size=280, chunk_overlap=40)
    assert len(chunks) >= 3
    assert all(len(c) <= 280 for c in chunks)
    assert "第1节" in chunks[0]


def test_sync_courses_from_jw_upload_and_strict_user_isolation(
    study_db: FakeStudyDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeJw:
        def timetable(self, xnm: str, xqm: str):
            return [
                {"course": "高等数学", "teacher": "李教授"},
                {"course": "高等数学", "teacher": "张助教"},
                {"course": "大学物理", "teacher": "王老师"},
            ]

    @contextmanager
    def fake_source(tid: str, uid: str):
        yield FakeJw()

    monkeypatch.setattr(jw_account, "source_for", fake_source)
    synced = study_space.sync_courses_from_jw("t1", "u1", datetime(2026, 11, 13).date())
    names = {c["name"]: c for c in synced}
    assert set(names.keys()) == {"高等数学", "大学物理"}
    assert "张助教 / 李教授" == names["高等数学"]["teacher"]

    math_id = names["高等数学"]["id"]
    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[1])
    s1.shapes.title.text = "泰勒公式展开"
    s1.placeholders[1].text = "带佩亚诺余项的麦克劳林展开在零点求极限中非常常用。"
    buf = io.BytesIO()
    prs.save(buf)

    mat = study_space.add_file_material("t1", "u1", math_id, "ch3_taylor.pptx", buf.getvalue())
    assert mat["chunk_count"] >= 1
    assert mat["kind"] == "file"

    # 学生自己的混合检索可命中 PPT 片段
    hits = study_space.search_course_chunks("t1", "u1", "泰勒 佩亚诺余项", course_id=math_id)
    assert hits and "佩亚诺余项" in hits[0]["content"]

    # 其他同学与跨租户完全隔离
    assert study_space.list_courses("t1", "u2") == []
    assert study_space.search_course_chunks("t1", "u2", "泰勒公式") == []
    assert study_space.search_course_chunks("t2", "u1", "泰勒公式") == []
    with pytest.raises(ValueError, match="无权访问"):
        study_space.add_material("t1", "u2", math_id, "盗录", "越权测试")


def test_sm2_schedule_and_flashcard_generation_review(
    study_db: FakeStudyDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SM-2 曲线校验：重来回退，记得逐步拉长间隔
    now = datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc)
    reps, interval, ease, due = study_space.sm2_schedule(2, 6, 2.5, 1, now=now)
    assert (reps, interval) == (0, 0)
    assert ease < 2.5 and due == now + timedelta(minutes=10)

    r1, i1, e1, _ = study_space.sm2_schedule(0, 0, 2.5, 3, now=now)
    r2, i2, e2, _ = study_space.sm2_schedule(r1, i1, e1, 3, now=now)
    r3, i3, _, _ = study_space.sm2_schedule(r2, i2, e2, 4, now=now)
    assert (r1, i1) == (1, 1)
    assert (r2, i2) == (2, 6)
    assert r3 == 3 and i3 > i2

    c = study_space.create_course("t1", "u1", "线性代数")
    study_space.add_material("t1", "u1", c["id"], "矩阵秩", "矩阵行变换不改变矩阵的秩，满秩矩阵可逆。")

    monkeypatch.setattr(
        study_space,
        "_call_chat_text",
        lambda tid, sys_p, usr_p, **kw: (
            '```json\n'
            '[{"front": "初等行变换是否改变矩阵的秩？", "back": "不改变，且可逆矩阵左乘不改变秩。", "tags": "核心性质"},'
            ' {"front": "n 阶方阵可逆的充要条件", "back": "行列式非零或秩等于 n。", "tags": "必背"}]\n'
            '```'
        ),
    )
    cards = study_space.generate_flashcards("t1", "u1", c["id"], count=2)
    assert len(cards) == 2
    assert study_space.count_due_flashcards("t1", "u1") == 2

    reviewed = study_space.review_flashcard("t1", "u1", cards[0]["id"], 4)
    assert reviewed is not None and reviewed["reps"] == 1 and reviewed["interval_days"] == 1
    assert study_space.count_due_flashcards("t1", "u1") == 1
    assert len(study_space.list_flashcards("t1", "u1", c["id"], due_only=True)) == 1


def test_generate_and_grade_quiz_with_choice_short_and_fallback(
    study_db: FakeStudyDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = study_space.create_course("t1", "u1", "操作系统")
    study_space.add_material("t1", "u1", c["id"], "死锁章节", "死锁的四个必要条件：互斥、占有并等待、非抢占、循环等待。")

    quiz_json = (
        '[{"type":"choice","question":"下列哪项不是死锁的必要条件？",'
        '"options":["A. 互斥","B. 公平调度","C. 占有并等待","D. 循环等待"],'
        '"answer":"B","explanation":"公平调度不是死锁条件。"},'
        '{"type":"short","question":"简述银行家算法的核心思想","options":[],'
        '"answer":"分配资源前先检查系统是否处于安全状态","explanation":"安全序列避免死锁。"}]'
    )
    monkeypatch.setattr(study_space, "_call_chat_text", lambda *args, **kwargs: quiz_json)
    quiz = study_space.generate_quiz("t1", "u1", c["id"], count=2)
    assert len(quiz["questions"]) == 2
    # 返回给前端的题干不得提前泄露标准答案
    assert "answer" not in quiz["questions"][0]

    # 模拟 LLM 评分抛出异常时自动降级到关键词覆盖评分，同时单选题确定性判分
    monkeypatch.setattr(study_space, "_call_chat_text", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("llm down")))
    graded = study_space.grade_quiz(
        "t1",
        "u1",
        quiz["quiz_id"],
        {"q1": "B. 公平调度", "q2": "分配资源前先判断系统是否处于安全状态"},
    )
    assert graded["results"][0]["score"] == 10 and graded["results"][0]["correct"] is True
    assert graded["results"][1]["score"] >= 6 and graded["percent"] >= 80

    # 其他同学无法提交或偷窥该测验
    with pytest.raises(ValueError, match="不存在"):
        study_space.grade_quiz("t1", "u2", quiz["quiz_id"], {"q1": "B"})


def test_agent_study_materials_tool_and_gating(
    study_db: FakeStudyDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 未启用 DB 时不破坏默认可用工具集
    monkeypatch.setattr(agent, "_study_space_enabled", lambda: False)
    assert [t.name for t in agent.available_tools("t1", "u1", WorkspaceSettings())] == ["search_knowledge"]

    monkeypatch.setattr(agent, "_study_space_enabled", lambda: True)
    assert "study_course_materials" in [t.name for t in agent.available_tools("t1", "u1", WorkspaceSettings())]
    assert "study_course_materials" not in [t.name for t in agent.available_tools("t1", "", WorkspaceSettings())]  # 访客禁用

    c = study_space.create_course("t1", "u1", "数据结构")
    study_space.add_material("t1", "u1", c["id"], "第五讲_树与二叉树", "AVL 树任意节点左右子树高度差绝对值不超过 1。")
    ctx = agent.ToolContext(
        tenant_id="t1",
        user_id="u1",
        settings=WorkspaceSettings(),
        search_knowledge=lambda q: ("", []),
    )
    output = agent._study_materials(ctx, {"query": "AVL 平衡因子", "course_name": "数据结构"})
    assert output["count"] >= 1
    assert "AVL 树" in output["chunks"][0]["content"]
    assert any("伴学空间·数据结构" in s["source"] for s in ctx.sources)
