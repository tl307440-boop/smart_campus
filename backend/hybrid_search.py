"""混合检索里不依赖 ChromaDB 的部分：BM25、RRF 融合、智谱云端重排客户端。

这些算法与 eval_rag/ 评测里验证过的实现是同一份（评测直接 import 这里），
所以线上跑出的指标可以和 eval_rag/results/report.md 对得上。
全部是纯 Python，不引入 PyTorch，2G 内存的服务器也能跑。
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Hashable, Sequence
from dataclasses import dataclass

# RRF 的平滑常数。60 是原论文与多数实现的默认值，评测也用的这个值。
RRF_K = 60

_TOKEN = re.compile(r"[\u4e00-\u9fff]+|[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """中文按相邻两字切（单字落单时保留单字），英文数字按词。不依赖分词库。

    Example:
        >>> tokenize("国家奖学金 2025")
        ['国家', '家奖', '奖学', '学金', '2025']
    """
    tokens: list[str] = []
    for match in _TOKEN.finditer(unicodedata.normalize("NFKC", text).lower()):
        piece = match.group()
        if "\u4e00" <= piece[0] <= "\u9fff":
            tokens.extend([piece] if len(piece) == 1 else (piece[i:i + 2] for i in range(len(piece) - 1)))
        else:
            tokens.append(piece)
    return tokens


class BM25:
    """Okapi BM25。语料就是传进来的这批文档，IDF 也只在这批文档里算。"""

    def __init__(self, documents: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(d)) for d in documents]
        self.lengths = [sum(tf.values()) for tf in self.tfs]
        self.avg_len = sum(self.lengths) / max(len(self.lengths), 1)
        df: Counter[str] = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(documents)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = set(tokenize(query))
        out = []
        for tf, length in zip(self.tfs, self.lengths, strict=True):
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * length / self.avg_len) if self.avg_len else self.k1
            for term in terms:
                freq = tf.get(term)
                if freq:
                    score += self.idf[term] * freq * (self.k1 + 1) / (freq + norm)
            out.append(score)
        return out

    def top(self, query: str, k: int) -> list[tuple[int, float]]:
        """得分大于 0 的前 k 个文档下标。并列时靠前的先出，保证结果可复现。"""
        scores = self.scores(query)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [(i, scores[i]) for i in order if scores[i] > 0]


def rrf_fuse(rankings: Sequence[Sequence[Hashable]], k: int = RRF_K) -> list[tuple[Hashable, float]]:
    """倒数排名融合：每份名单里排得越靠前得分越高，各名单的得分相加。

    只用名次、不用原始分数，所以向量相似度和 BM25 这种量纲完全不同的分数可以直接融合。

    Example:
        >>> [key for key, _ in rrf_fuse([["a", "b"], ["b", "c"]])]
        ['b', 'a', 'c']
    """
    fused: dict[Hashable, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda item: item[1], reverse=True)


class RerankError(RuntimeError):
    """云端重排没拿到可用结果（超时、限流、返回格式不对……）。调用方应当降级而不是报错。"""


@dataclass(frozen=True)
class ZhipuRerankClient:
    """智谱云端 rerank 接口。不占本机内存。

    故意不做重试：线上问答有时延预算，失败就让调用方立刻退回 RRF 排序。
    """

    api_key: str
    timeout: float = 2.0
    url: str = "https://open.bigmodel.cn/api/paas/v4/rerank"
    # 接口单次最多 128 条文档，留一点余量
    max_documents: int = 100

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """返回与 documents 一一对应的相关性分数（越大越相关）。"""
        if not documents:
            return []
        if len(documents) > self.max_documents:
            raise RerankError(f"候选 {len(documents)} 条超过单次上限 {self.max_documents}")
        body = json.dumps(
            {"model": "rerank", "query": query, "documents": list(documents),
             "top_n": len(documents), "return_documents": False}
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RerankError(f"HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise RerankError(f"{type(exc).__name__}: {exc}") from None

        scores: list[float | None] = [None] * len(documents)
        try:
            for item in payload["results"]:
                scores[int(item["index"])] = float(item["relevance_score"])
        except (KeyError, TypeError, ValueError, IndexError):
            raise RerankError("返回格式不符合预期") from None
        if any(s is None for s in scores):
            raise RerankError("返回的分数条数不全")
        return [float(s) for s in scores]  # type: ignore[arg-type]
