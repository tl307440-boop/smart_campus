"""线上回归：用真实的 VectorRAGService.search_chunks 复跑评测集，核对线上代码跑出的指标与评测一致。

评测（run_eval）里的混合 / 重排是评测侧自己拼的对照方案；这里反过来，直接调用线上入口，
三种配置各跑一遍：

- old    旧线上逻辑：纯向量，无阈值，不重排（RETRIEVAL_HYBRID=false、min_score=0）
- step1  第 1 步：向量 + BM25 的 RRF 混合 + 题级门控（默认 0.45），不重排
- step2  第 1+2 步：再加智谱云端重排（RERANK_PROVIDER=zhipu）

语料有两份：
- before  接入 OCR 回退之前的语料，与评测报告同一份，用来和报告逐项对照
- after   接入之后的语料（两份扫描件的 OCR 原文入库），看 ocr_gap 那 5 题能不能答出来

用法（在 smart_campus 目录下）：

    $env:ZHIPU_API_KEY="..."            # 只用环境变量，不写进任何文件
    python -m eval_rag.run_regression

embedding、云端重排都走磁盘缓存；缓存没命中的才会真的请求智谱（量很小）。
search_chunks 内部吞掉的异常（限流、断网……）会被计数，只要有就判回归失败，
否则这些题会被悄悄算成「拒答」，污染指标。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from eval_rag.corpus import CACHE_DIR, load_production_corpus, ocr_extra_docs, ocr_sidecar_docs
from eval_rag.metrics import (
    Question,
    first_relevant_rank,
    hit_at_k,
    is_answerable,
    mean,
    paired_bootstrap,
)
from eval_rag.run_eval import RESULTS, load_questions

TOP_K = 3
# 评测报告里「同一方案」的指标，回归结果应当与之接近（差异来自线上用 HNSW 近似检索、评测用精确余弦）
REPORT_SCHEMES = {"old": "vector", "step1": "hybrid", "step2": "hybrid+zhipu-rerank"}
TOLERANCE_PT = 4.0

CORPUS_LABELS = {
    "before": "before：接入 OCR 之前（与评测报告同一份）",
    "after": "after：上线配置（随代码发布的 OCR 文本已入库）",
    "after_plus_extra": "after_plus_extra：对照实验，再加上本机 OCR 过但**未随代码发布**的扫描件",
}

MODES = {
    "old": {"label": "旧线上逻辑（纯向量，无阈值）", "hybrid": "false", "rerank": "none", "min_score": 0.0},
    "step1": {"label": "第 1 步：混合 + 题级门控 0.45", "hybrid": "true", "rerank": "none", "min_score": None},
    "step2": {"label": "第 1+2 步：+ 智谱云端重排", "hybrid": "true", "rerank": "zhipu", "min_score": None},
}


class ErrorCounter(logging.Handler):
    """数 vector_service 里记过几条 ERROR：search_chunks 会吞掉异常并返回空列表。"""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


class CountingReranker:
    """包一层评测用的带缓存云端重排，统计调用与失败次数。失败照常抛出，让线上的降级逻辑去处理。"""

    def __init__(self, inner) -> None:
        self.inner, self.calls, self.failures = inner, 0, 0

    def score(self, query: str, texts: list[str]) -> list[float]:
        self.calls += 1
        try:
            return self.inner.score(query, texts)
        except Exception:
            self.failures += 1
            raise


def run_mode(svc, questions: list[Question], mode: dict, reranker, tenant: str) -> dict[str, list[str]]:
    """{题号: 线上返回的 Top-3 片段文本}。"""
    os.environ["RETRIEVAL_HYBRID"] = mode["hybrid"]
    os.environ["RERANK_PROVIDER"] = mode["rerank"]
    svc.cloud_reranker = reranker if mode["rerank"] == "zhipu" else None
    out = {}
    for q in questions:
        chunks = svc.search_chunks(q.query, tenant, top_k=TOP_K, min_score=mode["min_score"])
        out[q.id] = [c.text for c in chunks]
    return out


def score_mode(questions: list[Question], retrieved: dict[str, list[str]]) -> dict:
    per_q = {}
    for q in questions:
        texts = retrieved[q.id]
        rank = first_relevant_rank(texts, q)
        per_q[q.id] = {"hit1": hit_at_k(rank, 1), "hit3": hit_at_k(rank, TOP_K), "refused": 1.0 if not texts else 0.0}
    answerable = [q for q in questions if is_answerable(q)]
    negatives = [q for q in questions if q.is_negative]
    ocr_gap = [q for q in questions if q.category == "ocr_gap"]
    return {
        "per_q": per_q,
        "answerable_n": len(answerable),
        "hit1": mean([per_q[q.id]["hit1"] for q in answerable]),
        "hit3": mean([per_q[q.id]["hit3"] for q in answerable]),
        "answerable_refused": mean([per_q[q.id]["refused"] for q in answerable]),
        "negative_n": len(negatives),
        "negative_refused": mean([per_q[q.id]["refused"] for q in negatives]),
        "ocr_gap_n": len(ocr_gap),
        "ocr_gap_hit3": mean([per_q[q.id]["hit3"] for q in ocr_gap]),
    }


def report_reference() -> dict[str, float]:
    """评测报告（raw 语料）里对应方案的 Hit@3，用来对照。没有 results.json 就返回空。"""
    path = RESULTS / "results.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))["results"]["raw"]
    questions = {q.id: q for q in load_questions()}
    out = {}
    for scheme in set(REPORT_SCHEMES.values()):
        ranks = [
            (v["rank"]) for qid, v in data[scheme].items() if is_answerable(questions[qid])
        ]
        out[scheme] = mean([hit_at_k(r, TOP_K) for r in ranks])
    return out


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def build_report(results: dict, reference: dict[str, float], health: dict) -> str:
    lines = [
        "# 线上回归：真实 `VectorRAGService.search_chunks` vs 评测报告",
        "",
        "直接调用线上入口，三种配置各跑一遍（Top-3，仅统计库里有答案的题；负例看是否整题拒答）。",
        "语料 **before** 与评测报告同一份；**after** 是接入扫描件 OCR 回退之后的语料。",
        "",
    ]
    for corpus_name, by_mode in results.items():
        base = by_mode["old"]
        lines += [
            f"## 语料：{CORPUS_LABELS.get(corpus_name, corpus_name)}",
            "",
            "| 配置 | Hit@1 | Hit@3 | Hit@3 相对旧逻辑 Δ [95% 区间] | 有答案题被拒 | 负例拒答 | OCR 缺口题 Hit@3 |",
            "|---|---|---|---|---|---|---|",
        ]
        for key, mode in MODES.items():
            r = by_mode[key]
            ids = [i for i in r["per_q"] if i in base["per_q"]]
            answerable_ids = [i for i in ids if i in base["answerable_ids"]]
            diffs = [r["per_q"][i]["hit3"] - base["per_q"][i]["hit3"] for i in answerable_ids]
            delta = "—" if key == "old" else "{:+.1f}pt [{:+.1f}, {:+.1f}]".format(
                *(v * 100 for v in paired_bootstrap(diffs))
            )
            lines.append(
                f"| {mode['label']} | {pct(r['hit1'])} | {pct(r['hit3'])} | {delta} | "
                f"{pct(r['answerable_refused'])} | {pct(r['negative_refused'])}（n={r['negative_n']}） | "
                f"{pct(r['ocr_gap_hit3'])}（n={r['ocr_gap_n']}） |"
            )
        lines.append("")

    if reference and "before" in results:
        lines += [
            "## 与评测报告对照（before 语料，Hit@3）",
            "",
            f"评测里向量检索用精确余弦，线上用 Chroma HNSW 近似检索，允许 ±{TOLERANCE_PT:.0f}pt 的差异。",
            "",
            "| 配置 | 线上入口实测 | 评测报告里的同类方案 | 差值 | 在容差内 |",
            "|---|---|---|---|---|",
        ]
        for key, scheme in REPORT_SCHEMES.items():
            if scheme not in reference:
                continue
            live, expected = results["before"][key]["hit3"], reference[scheme]
            diff = (live - expected) * 100
            lines.append(
                f"| {MODES[key]['label']} | {pct(live)} | {pct(expected)}（{scheme}） | {diff:+.1f}pt | "
                f"{'是' if abs(diff) <= TOLERANCE_PT else '**否**'} |"
            )
        lines.append("")

    if "after" in results and "after_plus_extra" in results:
        shipped, plus = results["after"], results["after_plus_extra"]
        lines += [
            "## 为什么湖北省资助资金管理办法的 OCR 没有随代码发布",
            "",
            f"两份扫描件里，只有国家奖学金评审办法（约 2.5k 字，9 个片段）随代码发布。对照实验里再加上"
            f"湖北省那份（85 页、约 108 个片段）：混合 + 门控的 Hit@3 从 {pct(shipped['step1']['hit3'])} 掉到 "
            f"{pct(plus['step1']['hit3'])}，加云端重排后从 {pct(shipped['step2']['hit3'])} 掉到 {pct(plus['step2']['hit3'])}，"
            f"而 OCR 缺口题的命中率没有任何提升（{pct(shipped['step1']['ocr_gap_hit3'])} → {pct(plus['step1']['ocr_gap_hit3'])}），"
            "因为这 5 题的证据全部在国家奖学金评审办法里。",
            "",
            "湖北那份大文件里有大量与本科院校无关的内容（中职、研究生、义务教育资助），会挤占本校奖助学金问题的 Top-3。",
            "**局限**：评测集是针对接入之前的语料标注的，新文档里的正确答案不会被算作命中，所以退步幅度里"
            "有一部分是标注覆盖不到（例如 q058、q061，OCR 片段里确实写着答案）；另一部分是真干扰（例如 q096「学校是公办还是民办」"
            "被带到湖北文件里的民办学校补助条款）。评测集里没有只能从湖北那份文件回答的题，所以也没法证明它有收益。",
            "想上线这份文件：把 OCR 文本放进 `data/ocr/`（文件名是 PDF 主名加 `.txt`）即可；建议先补一批只能从它回答的题再评估。",
            "",
        ]

    lines += [
        "## 运行健康度",
        "",
        f"- 检索过程中被吞掉的异常数：{health['search_errors']}（应为 0）",
        f"- 云端重排调用 {health['rerank_calls']} 次，失败 {health['rerank_failures']} 次（失败会让该题降级为融合排序）",
        f"- 语料对照：before 语料与评测缓存快照{'一致' if health['before_matches_snapshot'] else '**不一致**'}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", choices=["before", "after", "after_plus_extra", "both"], default="both",
                        help="both = 全部语料版本")
    parser.add_argument("--refresh-corpus", action="store_true", help="重新读取 wiki/PDF（读 22MB 的手册约 20 秒）")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not key:
        print("缺少环境变量 ZHIPU_API_KEY", file=sys.stderr)
        return 2
    # 干净的起点：不让本机 .env 里的设置影响回归
    os.environ.pop("RAG_MIN_SCORE_DEFAULT", None)
    os.environ.pop("RERANK_CANDIDATE_K", None)

    from vector_service import BatchedZhipuEmbeddings
    from vector_service import logger as service_logger

    from eval_rag.retrievers import (
        EVAL_TENANT,
        CachedEmbeddings,
        EvalIndex,
        TimingLog,
        ZhipuReranker,
    )

    questions = load_questions()
    after = load_production_corpus(refresh=args.refresh_corpus, cache_name="corpus_after")
    sidecars = ocr_sidecar_docs()
    before = {name: text for name, text in after.items() if name not in sidecars}
    snapshot = json.loads((CACHE_DIR / "corpus.json").read_text(encoding="utf-8"))
    # 旧代码把抽不出文字的扫描件登记成了空文档，现在直接跳过；内容没变，比较时忽略空文档
    before_matches = before == {name: text for name, text in snapshot.items() if text.strip()}
    print(f"after 语料 {len(after)} 份文档；其中靠 OCR 回退入库：{sorted(sidecars & after.keys())}")
    print(f"before 语料与评测快照一致：{before_matches}")

    # 变体名就是 Chroma 里的 collection 后缀；raw 复用评测报告建好的那份索引
    corpora = {"before": ("raw", before), "after": ("shipped", after)}
    extra = ocr_extra_docs()
    if extra:
        # 对照实验：把本机 OCR 过、但没随代码发布的扫描件也加进去，看值不值得上线
        corpora["after_plus_extra"] = ("plus_extra", {**after, **extra})
    if args.corpus != "both":
        corpora = {args.corpus: corpora[args.corpus]}

    timing = TimingLog()
    embeddings = CachedEmbeddings(BatchedZhipuEmbeddings(model="embedding-3", api_key=key), "embedding-3", timing)
    reranker = CountingReranker(ZhipuReranker(key, timing))
    errors = ErrorCounter()
    service_logger.addHandler(errors)
    persist = CACHE_DIR / "chroma-embedding-3"

    results: dict[str, dict] = {}
    for corpus_name, (variant, corpus) in corpora.items():
        print(f"== 语料 {corpus_name}：建索引 ...")
        index = EvalIndex(variant, corpus, embeddings, persist)
        results[corpus_name] = {}
        answerable_ids = {q.id for q in questions if is_answerable(q)}
        for mode_key, mode in MODES.items():
            retrieved = run_mode(index.svc, questions, mode, reranker, EVAL_TENANT)
            scored = score_mode(questions, retrieved)
            scored["answerable_ids"] = answerable_ids
            results[corpus_name][mode_key] = scored
            print(f"   {mode['label']}: Hit@3 {pct(scored['hit3'])}，负例拒答 {pct(scored['negative_refused'])}，"
                  f"OCR 缺口 Hit@3 {pct(scored['ocr_gap_hit3'])}")
    timing.flush()
    embeddings.cache.flush()

    health = {
        "search_errors": len(errors.records),
        "rerank_calls": reranker.calls,
        "rerank_failures": reranker.failures,
        "before_matches_snapshot": before_matches,
    }
    reference = report_reference()
    report = build_report(results, reference, health)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "regression.md").write_text(report, encoding="utf-8")
    slim = {c: {m: {k: v for k, v in r.items() if k not in {"answerable_ids"}} for m, r in by.items()}
            for c, by in results.items()}
    (RESULTS / "regression.json").write_text(json.dumps({"health": health, "results": slim}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成：{RESULTS / 'regression.md'}")

    problems = []
    if errors.records:
        problems.append(f"检索中有 {len(errors.records)} 次异常被吞掉，例如：{errors.records[0][:120]}")
    if reranker.failures:
        problems.append(f"云端重排失败 {reranker.failures} 次")
    if not before_matches:
        problems.append("before 语料与评测快照不一致")
    if "before" in results:
        for key, scheme in REPORT_SCHEMES.items():
            if scheme in reference and abs(results["before"][key]["hit3"] - reference[scheme]) * 100 > TOLERANCE_PT:
                problems.append(f"{MODES[key]['label']} 与评测报告差异超出容差")
    for p in problems:
        print("回归失败：", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
