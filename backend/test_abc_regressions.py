"""A/B/C 评审项回归：tid 优先级、content_hash 跳过索引、emotion 写入 messages。"""
import unittest
from unittest.mock import MagicMock, patch

from graph_app import (
    MAX_STORED_TURNS,
    _append_turn_messages,
    emotion_node,
    finalize_node,
    recent_history,
)
from session_thread import normalize_tid, resolve_thread_id
from tenancy import GLOBAL_TENANT, source_filter, visibility_filter
from vector_service import RetrievedChunk, VectorRAGService, document_content_hash

TENANT = "tenantaaa111"


class TestThreadIdResolve(unittest.TestCase):
    def test_url_overrides_session(self):
        tid, restore, sync = resolve_thread_id("session-a", "url-b")
        self.assertEqual(tid, "url-b")
        self.assertTrue(restore)
        self.assertTrue(sync)

    def test_same_tid_no_restore(self):
        tid, restore, sync = resolve_thread_id("same", "same")
        self.assertEqual(tid, "same")
        self.assertFalse(restore)
        self.assertFalse(sync)

    def test_session_without_url_syncs(self):
        tid, restore, sync = resolve_thread_id("session-a", None)
        self.assertEqual(tid, "session-a")
        self.assertFalse(restore)
        self.assertTrue(sync)

    def test_normalize_list(self):
        self.assertEqual(normalize_tid(["abc"]), "abc")


class TestContentHash(unittest.TestCase):
    def test_hash_changes_with_content(self):
        self.assertEqual(document_content_hash("校园手册"), document_content_hash("校园手册"))
        self.assertNotEqual(document_content_hash("校园手册"), document_content_hash("校园手册v2"))

    def test_index_skips_when_hash_unchanged(self):
        svc = VectorRAGService.__new__(VectorRAGService)
        svc.collection = MagicMock()
        svc.embedding_model = MagicMock()
        content = "短文档内容"
        svc.collection.get.return_value = {
            "ids": ["id1"],
            "metadatas": [{"source": "a.txt", "content_hash": document_content_hash(content)}],
        }
        svc.index_document(content, "a.txt", TENANT)
        svc.embedding_model.embed_documents.assert_not_called()
        svc.collection.add.assert_not_called()

    def test_index_rebuilds_when_hash_changed(self):
        svc = VectorRAGService.__new__(VectorRAGService)
        svc.collection = MagicMock()
        svc.embedding_model = MagicMock()
        svc.embedding_model.embed_documents.return_value = [[0.1, 0.2]]
        svc.collection.get.return_value = {
            "ids": ["id1"],
            "metadatas": [{"source": "a.txt", "content_hash": document_content_hash("旧内容")}],
        }
        svc.index_document("新内容", "a.txt", TENANT)
        svc.collection.delete.assert_called_once_with(where=source_filter("a.txt", TENANT))
        svc.embedding_model.embed_documents.assert_called_once()
        svc.collection.add.assert_called_once()

    def test_indexed_chunks_carry_owner(self):
        """写入的每个片段都要带归属，否则检索期的可见性过滤会漏掉它。"""
        svc = VectorRAGService.__new__(VectorRAGService)
        svc.collection = MagicMock()
        svc.embedding_model = MagicMock()
        svc.embedding_model.embed_documents.return_value = [[0.1, 0.2]]
        svc.collection.get.return_value = {"ids": [], "metadatas": []}

        svc.index_document("一些内容", "a.txt", TENANT)

        metadatas = svc.collection.add.call_args.kwargs["metadatas"]
        self.assertTrue(all(m["tenant_id"] == TENANT for m in metadatas))

    def test_same_name_in_different_tenants_does_not_collide(self):
        """两校上传同名文件时，查已有索引的条件必须互不相同。"""
        self.assertNotEqual(
            source_filter("手册.pdf", TENANT), source_filter("手册.pdf", "tenantbbb222")
        )


class TestRerankPipeline(unittest.TestCase):
    def _service_with_candidates(self):
        svc = VectorRAGService.__new__(VectorRAGService)
        svc.embedding_model = MagicMock()
        svc.embedding_model.embed_query.return_value = [0.1, 0.2]
        svc.collection = MagicMock()
        svc.collection.count.return_value = 3
        svc.collection.query.return_value = {
            "documents": [["门禁22点", "奖学金按绩点", "图书馆开放"]],
            "metadatas": [[{"source": "a.txt"}, {"source": "b.txt"}, {"source": "c.txt"}]],
            "distances": [[0.1, 0.4, 0.8]],
        }
        return svc

    def test_retrieve_candidates_wide_recall(self):
        svc = self._service_with_candidates()
        chunks = svc.retrieve_candidates("宿舍门禁", TENANT, k=50)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].text, "门禁22点")
        self.assertGreater(chunks[0].vector_score, chunks[2].vector_score)

    def test_recall_is_restricted_to_visible_tenants(self):
        """召回必须带 where 过滤，否则别校上传的资料会被检索出来。"""
        svc = self._service_with_candidates()
        svc.retrieve_candidates("宿舍门禁", TENANT, k=50)

        where = svc.collection.query.call_args.kwargs["where"]
        self.assertEqual(where, visibility_filter(TENANT))
        self.assertEqual(set(where["tenant_id"]["$in"]), {GLOBAL_TENANT, TENANT})

    @patch("vector_service.get_cross_encoder")
    def test_rerank_uses_cross_encoder_scores(self, mock_get):
        reranker = MagicMock()
        reranker.predict.return_value = [0.1, 0.9, 0.2]
        mock_get.return_value = reranker
        svc = VectorRAGService.__new__(VectorRAGService)
        candidates = [
            RetrievedChunk("弱相关", {"source": "a.txt"}, vector_score=0.99),
            RetrievedChunk("最相关", {"source": "b.txt"}, vector_score=0.10),
            RetrievedChunk("一般", {"source": "c.txt"}, vector_score=0.50),
        ]
        top = svc.rerank_candidates("奖学金怎么评", candidates, top_n=2)
        self.assertEqual([c.text for c in top], ["最相关", "一般"])
        self.assertEqual(top[0].metadata["rerank_score"], 0.9)

    @patch("vector_service.get_cross_encoder", return_value=None)
    def test_search_falls_back_to_vector_top_k(self, _mock_get):
        svc = self._service_with_candidates()
        context = svc.search("宿舍门禁", TENANT, top_k=2)
        self.assertIn("门禁22点", context)
        self.assertIn("奖学金按绩点", context)
        self.assertNotIn("图书馆开放", context)


class TestEmotionAndMessages(unittest.TestCase):
    def test_append_turn_keeps_context(self):
        state = {"messages": []}
        msgs = _append_turn_messages(state, "奖学金？", "按手册评定", context="来源片段A")
        self.assertEqual(msgs[-1]["role"], "assistant")
        self.assertEqual(msgs[-1]["context"], "来源片段A")

    @patch("graph_app.get_deepseek")
    def test_emotion_node_only_returns_data(self, mock_get):
        """情感节点与生成并行跑，那时 assistant 消息还没写进去，不能碰 messages。"""
        mock_ds = MagicMock()
        mock_ds.analyze_emotion_and_reply.return_value = {"core_emotion": "平静"}
        mock_get.return_value = mock_ds

        out = emotion_node({"user_question": "你好", "messages": []})

        self.assertEqual(out["emotion_data"]["core_emotion"], "平静")
        self.assertNotIn("messages", out)

    def test_finalize_attaches_emotion_to_last_assistant(self):
        state = {
            "emotion_data": {"core_emotion": "平静", "reply_text": "我在听"},
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "嗨", "context": ""},
            ],
        }
        out = finalize_node(state)
        self.assertEqual(out["messages"][-1]["emotion"]["reply_text"], "我在听")

    def test_finalize_is_noop_without_emotion(self):
        state = {"messages": [{"role": "assistant", "content": "嗨"}]}
        self.assertEqual(finalize_node(state), {})

    def test_history_sent_to_model_is_capped(self):
        """全量历史发出去会让 token 成本随轮次线性上涨。"""
        messages = []
        for i in range(50):
            messages.append({"role": "user", "content": f"问题{i}"})
            messages.append({"role": "assistant", "content": f"回答{i}", "context": ""})

        trimmed = recent_history(messages, max_turns=3)

        self.assertEqual(len(trimmed), 6)
        self.assertEqual(trimmed[-1]["content"], "回答49")
        # 只保留 role/content，context 与 emotion 不该塞进提示词
        self.assertEqual(set(trimmed[0]), {"role", "content"})

    def test_stored_history_is_capped(self):
        """存进 Redis 的快照也要裁剪，否则 state 随会话无限膨胀。"""
        state = {"messages": [{"role": "user", "content": "x"} for _ in range(500)]}
        msgs = _append_turn_messages(state, "新问题", "新回答")

        self.assertLessEqual(len(msgs), MAX_STORED_TURNS * 2)
        self.assertEqual(msgs[-1]["content"], "新回答")


class TestUploadFilename(unittest.TestCase):
    def test_strips_path_traversal(self):
        from knowledge import _safe_filename

        self.assertEqual(_safe_filename("../etc/passwd.txt"), "passwd.txt")
        self.assertEqual(_safe_filename("奖学金 手册.PDF"), "奖学金_手册.pdf")
        self.assertEqual(_safe_filename("要点.md"), "要点.md")

    def test_rejects_unsupported_ext(self):
        from knowledge import _safe_filename

        with self.assertRaises(ValueError):
            _safe_filename("hack.exe")


class TestWikiHelpers(unittest.TestCase):
    def test_strip_frontmatter(self):
        from knowledge import _strip_frontmatter

        raw = "---\ntitle: 测\nstatus: current\n---\n\n# 正文\n金额 10000\n"
        self.assertEqual(_strip_frontmatter(raw), "# 正文\n金额 10000\n")
        self.assertEqual(_strip_frontmatter("# 无头\n"), "# 无头\n")

    def test_wiki_dir_helpers(self):
        from knowledge import _WIKI_SKIP_NAMES, _wiki_dir

        self.assertTrue(_wiki_dir().replace("\\", "/").endswith("/wiki"))
        self.assertIn("agents.md", _WIKI_SKIP_NAMES)
        self.assertIn("log.md", _WIKI_SKIP_NAMES)


if __name__ == "__main__":
    unittest.main()
