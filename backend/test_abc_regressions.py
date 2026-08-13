"""A/B/C 评审项回归：tid 优先级、content_hash 跳过索引、emotion 写入 messages。"""
import unittest
from unittest.mock import MagicMock, patch

from session_thread import normalize_tid, resolve_thread_id
from vector_service import RetrievedChunk, VectorRAGService, document_content_hash
from graph_app import _append_turn_messages, emotion_node


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
        svc.index_document(content, "a.txt")
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
        svc.index_document("新内容", "a.txt")
        svc.collection.delete.assert_called_once_with(where={"source": "a.txt"})
        svc.embedding_model.embed_documents.assert_called_once()
        svc.collection.add.assert_called_once()


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
        chunks = svc.retrieve_candidates("宿舍门禁", k=50)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].text, "门禁22点")
        self.assertGreater(chunks[0].vector_score, chunks[2].vector_score)

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
        context = svc.search("宿舍门禁", top_k=2)
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
    def test_emotion_node_attaches_to_last_assistant(self, mock_get):
        mock_ds = MagicMock()
        mock_ds.analyze_emotion_and_reply.return_value = {
            "core_emotion": "平静",
            "reply_text": "我在听",
        }
        mock_get.return_value = mock_ds
        state = {
            "user_question": "你好",
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "嗨", "context": ""},
            ],
        }
        out = emotion_node(state)
        self.assertEqual(out["emotion_data"]["core_emotion"], "平静")
        self.assertEqual(out["messages"][-1]["emotion"]["reply_text"], "我在听")


if __name__ == "__main__":
    unittest.main()
