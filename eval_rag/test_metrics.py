"""评测指标的单元测试，纯离线。在 smart_campus 目录下运行：python -m pytest eval_rag/test_metrics.py"""
import unittest

from eval_rag.metrics import (
    Question,
    auc,
    find_noise_lines,
    first_relevant_rank,
    hit_at_k,
    is_answerable,
    is_relevant,
    noise_ratio,
    normalize,
    paired_bootstrap,
    reciprocal_rank,
    strip_noise,
)


def make_question(evidence, category="xueji"):
    return Question.from_dict(
        {"id": "t1", "category": category, "query": "q", "evidence": evidence}
    )


class TestNormalize(unittest.TestCase):
    def test_fullwidth_punctuation_and_spaces(self):
        self.assertEqual(normalize("病假不得超过30 天，事假不得超过15 天"), "病假不得超过30天,事假不得超过15天")

    def test_linebreak_inside_sentence_is_removed(self):
        self.assertEqual(normalize("可以向湖北\n省教育厅提出"), "可以向湖北省教育厅提出")

    def test_fullwidth_digits_and_percent(self):
        self.assertEqual(normalize("不超过１５％"), "不超过15%")


class TestRelevance(unittest.TestCase):
    def test_any_group_all_anchors(self):
        q = make_question([["国家奖学金", "10000元"], ["仅此一句"]])
        self.assertTrue(is_relevant("（一）国家奖学金 ……奖励标准为 10000 元/人", q))
        self.assertFalse(is_relevant("国家奖学金……8000元", q))
        self.assertTrue(is_relevant("出现了仅此一句", q))

    def test_question_without_evidence_never_relevant(self):
        q = make_question([], category="negative")
        self.assertFalse(is_relevant("任何文本", q))

    def test_rank_is_one_based_and_none_when_missing(self):
        q = make_question([["目标"]])
        self.assertEqual(first_relevant_rank(["无关", "含有目标的片段", "目标"], q), 2)
        self.assertIsNone(first_relevant_rank(["无关"], q))

    def test_answerable_excludes_negative_and_ocr_gap(self):
        self.assertTrue(is_answerable(make_question([["a"]], "xueji")))
        self.assertFalse(is_answerable(make_question([["a"]], "ocr_gap")))
        self.assertFalse(is_answerable(make_question([], "negative")))


class TestRankMetrics(unittest.TestCase):
    def test_hit_and_mrr(self):
        self.assertEqual(hit_at_k(3, 3), 1.0)
        self.assertEqual(hit_at_k(4, 3), 0.0)
        self.assertEqual(hit_at_k(None, 5), 0.0)
        self.assertAlmostEqual(reciprocal_rank(4), 0.25)
        self.assertEqual(reciprocal_rank(11, depth=10), 0.0)
        self.assertEqual(reciprocal_rank(None), 0.0)

    def test_auc_perfect_random_and_ties(self):
        self.assertEqual(auc([0.9, 0.8], [0.1, 0.2]), 1.0)
        self.assertEqual(auc([0.1], [0.9]), 0.0)
        self.assertEqual(auc([0.5], [0.5]), 0.5)

    def test_bootstrap_interval_contains_mean_and_is_deterministic(self):
        diffs = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        mean_diff, lo, hi = paired_bootstrap(diffs, n_resamples=500)
        self.assertAlmostEqual(mean_diff, 0.3)
        self.assertLessEqual(lo, mean_diff)
        self.assertGreaterEqual(hi, mean_diff)
        self.assertEqual(paired_bootstrap(diffs, n_resamples=500), (mean_diff, lo, hi))

    def test_bootstrap_all_zero_has_zero_width(self):
        self.assertEqual(paired_bootstrap([0.0] * 20, n_resamples=200), (0.0, 0.0, 0.0))


class TestWatermark(unittest.TestCase):
    def setUp(self):
        page = "第一条 正文内容\n" + "武凌\n" * 24 + "—3—\n"
        self.text = page * 3 + "（一）\n" * 10

    def test_finds_repeated_short_lines_only(self):
        noise = find_noise_lines(self.text)
        self.assertIn("武凌", noise)
        self.assertNotIn("（一）", noise)  # 序号行不算噪声
        self.assertNotIn("—3—", noise)  # 页码行不算噪声
        self.assertNotIn("第一条 正文内容", noise)

    def test_strip_and_ratio(self):
        noise = {"武凌"}
        cleaned = strip_noise("正文\n武凌\n 武凌\n\n", noise)
        self.assertEqual(cleaned, "正文")
        self.assertAlmostEqual(noise_ratio("正文\n武凌", noise), 2 / 4)
        self.assertEqual(noise_ratio("", noise), 0.0)


if __name__ == "__main__":
    unittest.main()
