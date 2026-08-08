"""A/B/C 评审项回归：tid 优先级、content_hash 跳过索引、emotion 写入 messages。"""
import unittest
from unittest.mock import MagicMock, patch

from session_thread import normalize_tid, resolve_thread_id
from vector_service import VectorRAGService, document_content_hash
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
