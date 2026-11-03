"""检索评测的纯函数：文本规范化、命中判定、指标与置信区间。

不依赖 ChromaDB / 网络，方便单独测试（见 test_metrics.py）。

命中判定的思路：每道题的标准答案不是「某个 chunk 的编号」，而是原文里的
几句短证据（锚点）。一个片段只要包含某一组锚点的全部内容，就算命中。
这样切片参数一变，评测集依然有效，不用重新标注。
"""
from __future__ import annotations

import random
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """NFKC 统一全角半角标点，再去掉所有空白。

    PDF 抽出来的中文常在句子中间断行、夹空格（如「8 年」），
    两边都规范化之后才能按子串比较。
    """
    return _WHITESPACE.sub("", unicodedata.normalize("NFKC", text))


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    style: str
    query: str
    evidence: tuple[tuple[str, ...], ...] = ()
    absent: tuple[str, ...] = ()
    # 规范化后的证据，构造时算一次，判定时反复用
    norm_evidence: tuple[tuple[str, ...], ...] = field(default=(), compare=False)

    @classmethod
    def from_dict(cls, raw: dict) -> Question:
        evidence = tuple(tuple(group) for group in raw.get("evidence", []))
        return cls(
            id=raw["id"],
            category=raw["category"],
            style=raw.get("style", "direct"),
            query=raw["query"],
            evidence=evidence,
            absent=tuple(raw.get("absent", [])),
            norm_evidence=tuple(tuple(normalize(a) for a in group) for group in evidence),
        )

    @property
    def is_negative(self) -> bool:
        return self.category == "negative"


# 主指标只统计「库里确实有答案」的题；负例单独看拒答，ocr_gap 单独看覆盖缺口
ANSWERABLE_EXCLUDED = {"negative", "ocr_gap"}


def is_answerable(question: Question) -> bool:
    return question.category not in ANSWERABLE_EXCLUDED


def is_relevant(chunk_text: str, question: Question, *, normalized: bool = False) -> bool:
    """片段含有任意一组证据的全部锚点，即相关。"""
    if not question.norm_evidence:
        return False
    text = chunk_text if normalized else normalize(chunk_text)
    return any(all(anchor in text for anchor in group) for group in question.norm_evidence)


def first_relevant_rank(texts: list[str], question: Question) -> int | None:
    """第一个相关片段的名次（从 1 开始），没有则 None。"""
    for rank, text in enumerate(texts, start=1):
        if is_relevant(text, question):
            return rank
    return None


def hit_at_k(rank: int | None, k: int) -> float:
    return 1.0 if rank is not None and rank <= k else 0.0


def reciprocal_rank(rank: int | None, depth: int = 10) -> float:
    return 1.0 / rank if rank is not None and rank <= depth else 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def auc(positives: list[float], negatives: list[float]) -> float:
    """随机取一个正例、一个负例，正例得分更高的概率（相同得分算一半）。

    用来衡量「最高分」能不能把有答案的问题和无答案的问题分开：
    0.5 等于瞎猜，1.0 是完美可分。
    """
    if not positives or not negatives:
        return float("nan")
    wins = 0.0
    for p in positives:
        for n in negatives:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def paired_bootstrap(
    diffs: list[float], n_resamples: int = 5000, seed: int = 20261103
) -> tuple[float, float, float]:
    """逐题配对差值的均值及 95% 自助法置信区间。

    评测集只有一百来题，几个百分点的差异很可能是抽样噪声；
    区间不含 0 才说明差异站得住。
    """
    if not diffs:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    size = len(diffs)
    means = sorted(
        sum(diffs[rng.randrange(size)] for _ in range(size)) / size for _ in range(n_resamples)
    )
    lo = means[int(0.025 * n_resamples)]
    hi = means[int(0.975 * n_resamples) - 1]
    return mean(diffs), lo, hi


# ---------- 水印噪声 ----------

def find_noise_lines(text: str, min_repeat: int = 8, max_len: int = 12) -> set[str]:
    """找出文档里反复出现的短行（PDF 水印、竖排页眉被拆成的单字行）。

    只看「短」而且「重复很多次」的行：条文里偶尔出现的短行不会被误伤，
    单独成行的序号「（一）」这类也排除掉。
    """
    counts = Counter(line.strip() for line in text.splitlines() if line.strip())
    enumerator = re.compile(r"^[(（]?[一二三四五六七八九十百\d]+[)）]?[.、]?$")
    return {
        line
        for line, count in counts.items()
        if count >= min_repeat
        and len(line) <= max_len
        and not enumerator.match(line)
        and not re.fullmatch(r"[—－\-\d\s]+", line)  # 页码行
    }


def strip_noise(text: str, noise: set[str]) -> str:
    kept = [line for line in text.splitlines() if line.strip() and line.strip() not in noise]
    return "\n".join(kept)


def noise_ratio(chunk_text: str, noise: set[str]) -> float:
    """片段里有多大比例的字符属于水印行。"""
    lines = [line for line in chunk_text.splitlines() if line.strip()]
    total = sum(len(line.strip()) for line in lines)
    if total == 0:
        return 0.0
    bad = sum(len(line.strip()) for line in lines if line.strip() in noise)
    return bad / total
