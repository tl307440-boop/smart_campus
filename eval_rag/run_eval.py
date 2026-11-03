"""RAG 检索评测：对比纯向量 / 阈值 / BM25 / 混合 / 重排，以及去水印预处理。

用法（在 smart_campus 目录下，用装好后端依赖的 Python）：

    set ZHIPU_API_KEY=你的智谱密钥          # PowerShell: $env:ZHIPU_API_KEY="..."
    python -m eval_rag.run_eval --validate-only   # 只校验评测集，不花钱
    python -m eval_rag.run_eval                   # 完整评测，写 eval_rag/results/
    python -m eval_rag.run_eval --local-rerank BAAI/bge-reranker-base   # 额外测本地 BGE 重排

密钥只从环境变量读，不会写进任何文件。embedding 与重排结果按内容哈希缓存在
eval_rag/.cache/，重跑不会重复计费。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

from eval_rag.corpus import (
    BACKEND,
    CACHE_DIR,
    clean_corpus,
    load_ocr_texts,
    load_production_corpus,
)
from eval_rag.metrics import (
    Question,
    auc,
    first_relevant_rank,
    hit_at_k,
    is_answerable,
    is_relevant,
    mean,
    noise_ratio,
    normalize,
    paired_bootstrap,
    reciprocal_rank,
)

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.jsonl"
RESULTS = HERE / "results"
THRESHOLDS = [0.0, 0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7]
PRODUCTION_TOP_K = 3
DEPTH = 10


def load_questions() -> list[Question]:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("评测集里有重复的题目编号")
    return [Question.from_dict(r) for r in rows]


# ---------- 评测集校验 ----------

def _chunks_of(corpus: dict[str, str]) -> list[tuple[str, str]]:
    """按线上切片器切分，返回 [(文档名, 规范化后的片段)]。"""
    sys.path.insert(0, str(BACKEND))
    from vector_service import CHUNK_OVERLAP, CHUNK_SIZE, _splitter

    splitter = _splitter(CHUNK_SIZE, CHUNK_OVERLAP)
    return [(name, normalize(piece)) for name, text in corpus.items() for piece in splitter.split_text(text)]


def validate(questions: list[Question], variants: dict[str, dict[str, str]]) -> list[str]:
    """逐题确认：有答案的题在每个语料版本里都至少有一个片段含有答案；
    ocr_gap 题的答案只在线上没索引的 OCR 原文里；负例的关键词在语料里根本没有。"""
    problems: list[str] = []
    ocr = {name: normalize(text) for name, text in load_ocr_texts().items()}

    for variant, corpus in variants.items():
        chunks = _chunks_of(corpus)
        full_text = normalize("".join(corpus.values()))
        for q in questions:
            hits = [name for name, text in chunks if is_relevant(text, q, normalized=True)]
            if q.category == "negative":
                leaked = [w for w in q.absent if normalize(w) in full_text]
                if not q.absent:
                    problems.append(f"[{variant}] {q.id} 负例没有声明 absent 关键词")
                if leaked:
                    problems.append(f"[{variant}] {q.id} 负例关键词其实在语料里: {leaked}")
            elif q.category == "ocr_gap":
                in_ocr = any(is_relevant(t, q, normalized=True) for t in ocr.values())
                if hits:
                    problems.append(f"[{variant}] {q.id} 本应只在 OCR 原文里，但已被索引: {sorted(set(hits))}")
                if not in_ocr:
                    problems.append(f"[{variant}] {q.id} 在 OCR 原文里也找不到证据")
            else:
                if not hits:
                    problems.append(f"[{variant}] {q.id} 没有任何片段含有答案: {q.query}")
                elif len(hits) > 12:
                    problems.append(f"[{variant}] {q.id} 证据过宽，{len(hits)} 个片段都算相关: {q.query}")
    return problems


# ---------- 评测 ----------

def build_schemes(reranker_names: list[str]) -> list[tuple[str, str]]:
    schemes = [
        ("vector", "纯向量（线上现状）"),
        ("bm25", "BM25 关键词"),
        ("hybrid", "混合（向量 + BM25, RRF）"),
    ]
    for name in reranker_names:
        schemes.append((f"vector+{name}", f"向量 + 重排（{name}）"))
        schemes.append((f"hybrid+{name}", f"混合 + 重排（{name}）"))
    return schemes


def ranking_of(run, scheme: str):
    if scheme == "vector":
        return run.vector
    if scheme == "bm25":
        return run.bm25
    if scheme == "hybrid":
        return run.hybrid
    base, name = scheme.split("+", 1)
    return run.reranked[name][base]


def evaluate(questions, runs, schemes, noise: set[str]) -> dict:
    """{方案: {题号: {rank, top3_sources, top3_noise}}}"""
    out: dict[str, dict[str, dict]] = {}
    for key, _ in schemes:
        per_q = {}
        for q in questions:
            hits = ranking_of(runs[q.id], key)[:DEPTH]
            per_q[q.id] = {
                "rank": first_relevant_rank([h.text for h in hits], q),
                "top3_sources": [h.source for h in hits[:PRODUCTION_TOP_K]],
                "top3_noise": mean([noise_ratio(h.text, noise) for h in hits[:PRODUCTION_TOP_K]]),
            }
        out[key] = per_q
    return out


def summarize(questions, per_scheme: dict[str, dict], pick) -> dict:
    subset = [q for q in questions if pick(q)]
    ranks = [per_scheme[q.id]["rank"] for q in subset]
    return {
        "n": len(subset),
        "hit1": mean([hit_at_k(r, 1) for r in ranks]),
        "hit3": mean([hit_at_k(r, 3) for r in ranks]),
        "hit5": mean([hit_at_k(r, 5) for r in ranks]),
        "mrr": mean([reciprocal_rank(r, DEPTH) for r in ranks]),
    }


def threshold_sweep(questions, runs, scheme: str) -> list[dict]:
    """先按向量相似度阈值过滤，再（可选）按该方案排序取 Top-3，和线上 search_chunks 的顺序一致。"""
    rows = []
    for t in THRESHOLDS:
        answer_hits, answer_empty, neg_refused = [], [], []
        for q in questions:
            run = runs[q.id]
            ranked = run.vector if scheme == "vector" else run.reranked[scheme.split("+", 1)[1]]["vector"]
            kept = [h for h in ranked if (h.vector_score or 0.0) >= t][:PRODUCTION_TOP_K]
            if q.is_negative:
                neg_refused.append(1.0 if not kept else 0.0)
            elif is_answerable(q):
                rank = first_relevant_rank([h.text for h in kept], q)
                answer_hits.append(hit_at_k(rank, PRODUCTION_TOP_K))
                answer_empty.append(1.0 if not kept else 0.0)
        rows.append({
            "threshold": t,
            "answerable_hit3": mean(answer_hits),
            "answerable_empty": mean(answer_empty),
            "negative_refused": mean(neg_refused),
        })
    return rows


def top1_scores(questions, runs, getter) -> tuple[list[float], list[float]]:
    pos, neg = [], []
    for q in questions:
        ranked = getter(runs[q.id])
        if not ranked:
            continue
        if q.is_negative:
            neg.append(getter(runs[q.id])[0])
        elif is_answerable(q):
            pos.append(getter(runs[q.id])[0])
    return pos, neg


def quantile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(p * len(ordered)), len(ordered) - 1)] if ordered else float("nan")


# ---------- 报告 ----------

def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def build_report(ctx: dict) -> str:
    questions: list[Question] = ctx["questions"]
    schemes = ctx["schemes"]
    labels = dict(schemes)
    out: list[str] = ["# RAG 检索评测报告", ""]

    # 1. 设置
    cats: dict[str, int] = {}
    for q in questions:
        cats[q.category] = cats.get(q.category, 0) + 1
    answerable = [q for q in questions if is_answerable(q)]
    out += [
        "## 1. 评测设置", "",
        f"- 评测题：共 {len(questions)} 题，其中「库里有答案」{len(answerable)} 题，"
        f"负例（库里没有答案）{cats.get('negative', 0)} 题，OCR 缺口题 {cats.get('ocr_gap', 0)} 题。",
        "- 语料：与线上一致，wiki 摘要页 + 能抽出文字的 3 份 PDF（学生手册、学籍办法、奖助学金办法）；"
        "两份扫描件 PDF 线上抽不出文字，不在索引里。",
        f"- Embedding：智谱 embedding-3；切片：递归中文切片 500/80；候选池：50；线上默认 Top-K：{PRODUCTION_TOP_K}。",
        "- 命中判定：Top-K 片段里至少有一个包含该题的证据锚点（原文短句），不依赖切片位置。",
        "- 指标：Hit@K（Top-K 里是否有正确片段）、MRR@10（第一个正确片段名次的倒数的平均）。",
        "- 括号里的 Δ 是相对「纯向量」的 Hit@3 差值，及 95% 配对自助法置信区间；区间包含 0 说明差异可能只是抽样噪声。", "",
        "- 向量检索：评测里用**精确余弦**，保证两次运行结果一致。线上用的是 Chroma 的 HNSW 近似检索，"
        "下表量化了两者的差距（近似检索每次进程启动都会重建近邻图，第 50 名附近的候选会有出入）。", "",
        md_table(["语料版本", "片段数", "说明", "近似与精确 Top-3 完全一致", "50 个候选的重合度", "近似检索 Hit@3"], [
            [v, str(info["chunks"]), info["note"], pct(info["ann_same_top3"]), pct(info["ann_overlap"]), pct(info["ann_hit3"])]
            for v, info in ctx["variants"].items()
        ]), "",
    ]

    # 2. 主对比
    for variant, info in ctx["variants"].items():
        results = ctx["results"][variant]
        out += [f"## 2.{'ab'[list(ctx['variants']).index(variant)]} 方案对比 —— 语料版本：{variant}", "",
                f"（{info['note']}；仅统计库里有答案的 {len(answerable)} 题）", ""]
        base = results["vector"]
        rows = []
        for key, label in schemes:
            s = summarize(questions, results[key], is_answerable)
            diffs = [hit_at_k(results[key][q.id]["rank"], 3) - hit_at_k(base[q.id]["rank"], 3) for q in answerable]
            delta = "—" if key == "vector" else "{:+.1f}pt [{:+.1f}, {:+.1f}]".format(*(x * 100 for x in paired_bootstrap(diffs)))
            rows.append([label, pct(s["hit1"]), pct(s["hit3"]), pct(s["hit5"]), f"{s['mrr']:.3f}", delta])
        out += [md_table(["方案", "Hit@1", "Hit@3", "Hit@5", "MRR@10", "Hit@3 相对纯向量 Δ"], rows), ""]

    # 3. 分类拆解
    cat_order = [c for c in ["xueji", "jiangzhu", "handbook", "conflict", "general"] if c in cats]
    cat_names = {"xueji": "学籍学务", "jiangzhu": "奖助学金", "handbook": "手册独有", "conflict": "新旧冲突", "general": "学校概况"}
    for variant in ctx["variants"]:
        results = ctx["results"][variant]
        header = ["方案"] + [f"{cat_names[c]}({cats[c]})" for c in cat_order] + ["直白提问", "口语化提问"]
        rows = []
        for key, label in schemes:
            row = [label]
            for c in cat_order:
                row.append(pct(summarize(questions, results[key], lambda q, c=c: q.category == c)["hit3"]))
            for style in ("direct", "colloquial"):
                row.append(pct(summarize(questions, results[key], lambda q, s=style: is_answerable(q) and q.style == s)["hit3"]))
            rows.append(row)
        out += [f"## 3. 分类拆解（Hit@3）—— 语料版本：{variant}", "", md_table(header, rows), ""]

    # 4. OCR 缺口
    gap = [q for q in questions if q.category == "ocr_gap"]
    if gap:
        rows = []
        for variant in ctx["variants"]:
            for key, label in schemes:
                s = summarize(questions, ctx["results"][variant][key], lambda q: q.category == "ocr_gap")
                rows.append([variant, label, pct(s["hit5"])])
        out += ["## 4. OCR 缺口：扫描件原文未入库的题", "",
                f"这 {len(gap)} 题的答案只在两份扫描件的 OCR 原文里（国家奖学金评审办法、湖北省资助资金管理办法），"
                "线上索引里只有 wiki 的摘要，没有原文。任何检索方案都不可能答对，Hit@5 应当为 0%，这是**语料覆盖**问题，不是检索算法问题。", "",
                md_table(["语料版本", "方案", "Hit@5"], [r for r in rows if r[0] == list(ctx['variants'])[0]]), ""]

    # 5. 阈值
    for variant in ctx["variants"]:
        out += [f"## 5. 相似度阈值扫描 —— 语料版本：{variant}", "",
                "过滤规则与线上一致：先丢掉向量相似度低于阈值的候选，再排序取 Top-3。"
                "「有答案题被拒」= 过滤后一个片段都不剩；「负例拒答」= 对库里没有答案的问题成功返回空。", ""]
        for key in [s for s, _ in schemes if s == "vector" or s.startswith("vector+")]:
            rows = [
                [f"{r['threshold']:.2f}", pct(r["answerable_hit3"]), pct(r["answerable_empty"]), pct(r["negative_refused"])]
                for r in ctx["thresholds"][variant][key]
            ]
            out += [f"**{labels[key]}**", "", md_table(["阈值", "有答案题 Hit@3", "有答案题被拒", "负例拒答"], rows), ""]

    # 6. 拒答分离度
    out += ["## 6. 能不能靠分数识别「库里没答案」", "",
            "取每题最高分，比较「有答案的题」与「没答案的题」。AUC = 随机抽一对，有答案那题分数更高的概率（0.5 等于瞎猜，1.0 完全可分）。", ""]
    rows = []
    for variant, sep in ctx["separation"].items():
        for name, s in sep.items():
            rows.append([variant, name, f"{s['auc']:.3f}", f"{s['pos_p10']:.5f} / {s['pos_median']:.5f}",
                         f"{s['neg_median']:.5f} / {s['neg_max']:.5f}"])
    out += [md_table(["语料版本", "分数来源", "AUC", "有答案题 P10 / 中位数", "无答案题 中位数 / 最大值"], rows), "",
            "**注意**：负例只有 12 题，且都是明显不相干的话题（食堂、游泳馆、医保等），属于「容易」的负例。"
            "同领域但知识库没覆盖的问题（比如问研究生奖学金细则）会更难区分，这里的 AUC 偏乐观。"
            "云端重排分数几乎全部挤在 1.0 附近（保留 5 位小数才能看出差别），不适合直接拿来当拒答阈值，"
            "拒答建议仍用向量相似度。", ""]

    # 7. 水印噪声
    out += ["## 7. PDF 水印噪声：去掉之后有没有收益", "",
            f"原始 PDF 抽出的文本里每页混着约 20 行水印（`{'`、`'.join(sorted(ctx['noise_examples']))}` 等重复短行）。",
            "下表对比各方案在 raw（带水印）与 clean（去水印、重新切片重新向量化）两个语料上的 Hit@3，"
            "差值为 clean − raw，括号是 95% 配对自助法区间；同时列出 raw 下 Top-3 片段里水印字符的平均占比。", ""]
    rows = []
    for key, label in schemes:
        r, c = ctx["results"]["raw"][key], ctx["results"]["clean"][key]
        diffs = [hit_at_k(c[q.id]["rank"], 3) - hit_at_k(r[q.id]["rank"], 3) for q in answerable]
        d, lo, hi = (x * 100 for x in paired_bootstrap(diffs))
        rows.append([label, pct(mean([hit_at_k(r[q.id]["rank"], 3) for q in answerable])),
                     pct(mean([hit_at_k(c[q.id]["rank"], 3) for q in answerable])),
                     f"{d:+.1f}pt [{lo:+.1f}, {hi:+.1f}]",
                     pct(mean([v["top3_noise"] for v in r.values()]))])
    out += [md_table(["方案", "raw Hit@3", "clean Hit@3", "差值 (clean−raw)", "raw 下 Top-3 水印占比"], rows), ""]

    # 8. 耗时与成本
    t = ctx["timing"]
    out += ["## 8. 延迟与成本（来自真实调用的记录）", "", md_table(["项目", "数值"], t), ""]

    # 9. 失败样例
    out += ["## 9. 纯向量（线上现状）Top-3 没命中的题", ""]
    results = ctx["results"][list(ctx["variants"])[0]]
    best = ctx["best_key"]
    rows = []
    for q in answerable:
        if hit_at_k(results["vector"][q.id]["rank"], 3) == 0:
            fixed = [labels[k] for k, _ in schemes if k != "vector" and hit_at_k(results[k][q.id]["rank"], 3) == 1]
            rows.append([q.id, q.query, str(results["vector"][q.id]["rank"] or "—"),
                         "；".join(fixed) if fixed else "都没救回来"])
    out += [md_table(["题号", "问题", "纯向量首个正确名次", "哪些方案能救回 Top-3"], rows) if rows else "无。", ""]
    out += [f"（对照最佳方案：{labels[best]}）", ""]

    # 10. Kimi 精读
    k = ctx["kimi"]
    out += ["## 10. 未评测：Kimi 精读路径", "",
            f"线上先尝试把「短文全文」整体交给 Kimi 摘录，前提是该租户可见的短文（单篇 < {k['long_doc']} 字）"
            f"总长不超过预算 {k['budget']} 字，否则直接走向量检索。",
            f"当前语料里短文总长 **{k['short_total']} 字**，超出预算 {k['short_total'] - k['budget']} 字，"
            "所以 `short_corpus()` 返回空串，**Kimi 精读路径在现有知识库上不会被触发**，所有问题实际都走向量检索。"
            "既然线上并不走这条路，对它做对比没有意义；若将来想启用，需要先决定把哪一部分文档喂给 Kimi。", ""]
    return "\n".join(out)


# ---------- 主流程 ----------

def get_api_key() -> str | None:
    key = os.getenv("ZHIPU_API_KEY")
    if key:
        return key.strip()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--validate-only", action="store_true", help="只校验评测集，不调用任何外部接口")
    parser.add_argument("--refresh-corpus", action="store_true", help="重新读取 wiki/PDF，不用缓存")
    parser.add_argument("--local-rerank", action="append", default=[], metavar="MODEL",
                        help="额外评测本地交叉编码器，如 BAAI/bge-reranker-base（需 torch + sentence-transformers）")
    parser.add_argument("--no-zhipu-rerank", action="store_true", help="不评测智谱云端重排")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    questions = load_questions()
    raw_corpus = load_production_corpus(refresh=args.refresh_corpus)
    clean, noise = clean_corpus(raw_corpus)
    variants_corpus = {"raw": raw_corpus, "clean": clean}

    problems = validate(questions, variants_corpus)
    print(f"评测集 {len(questions)} 题，校验问题 {len(problems)} 个")
    for p in problems:
        print("  -", p)
    if problems:
        return 1
    if args.validate_only:
        return 0

    key = get_api_key()
    if not key:
        print("缺少环境变量 ZHIPU_API_KEY（embedding 与重排都要用）", file=sys.stderr)
        return 2

    from vector_service import BatchedZhipuEmbeddings

    from eval_rag.retrievers import (
        CachedEmbeddings,
        EvalIndex,
        LocalCrossEncoder,
        TimingLog,
        ZhipuReranker,
    )

    timing = TimingLog()
    rerankers = []
    if not args.no_zhipu_rerank:
        rerankers.append(ZhipuReranker(key, timing))
    for model in args.local_rerank:
        rerankers.append(LocalCrossEncoder(model, timing))
    schemes = build_schemes([r.name for r in rerankers])

    embeddings = CachedEmbeddings(BatchedZhipuEmbeddings(model="embedding-3", api_key=key), "embedding-3", timing)
    persist = CACHE_DIR / "chroma-embedding-3"

    variants_info, results, thresholds, separation = {}, {}, {}, {}
    notes = {"raw": "线上现状，PDF 文本带水印", "clean": "去掉水印行后重新切片、重新向量化"}
    for variant, corpus in variants_corpus.items():
        print(f"== 语料版本 {variant}: 建索引 ...")
        index = EvalIndex(variant, corpus, embeddings, persist)
        variants_info[variant] = {"chunks": index.chunk_count, "note": notes[variant]}
        print(f"   片段数 {index.chunk_count}，开始检索 {len(questions)} 题 ...")
        runs = {}
        for i, q in enumerate(questions, start=1):
            runs[q.id] = index.run(q.query, rerankers)
            if i % 20 == 0:
                print(f"   {i}/{len(questions)}")
        results[variant] = evaluate(questions, runs, schemes, noise)
        # 线上是 HNSW 近似检索，评测用精确检索；量化两者的差距
        same_top3 = [
            [h.text for h in runs[q.id].ann[:PRODUCTION_TOP_K]] == [h.text for h in runs[q.id].vector[:PRODUCTION_TOP_K]]
            for q in questions
        ]
        overlap = [
            len({h.text for h in runs[q.id].ann} & {h.text for h in runs[q.id].vector}) / max(len(runs[q.id].vector), 1)
            for q in questions
        ]
        ann_hit3 = mean([
            hit_at_k(first_relevant_rank([h.text for h in runs[q.id].ann[:DEPTH]], q), 3) for q in questions if is_answerable(q)
        ])
        variants_info[variant].update(
            ann_same_top3=mean([1.0 if s else 0.0 for s in same_top3]), ann_overlap=mean(overlap), ann_hit3=ann_hit3
        )
        thresholds[variant] = {
            k: threshold_sweep(questions, runs, k) for k, _ in schemes if k == "vector" or k.startswith("vector+")
        }
        separation[variant] = {}
        sources = {"向量相似度": lambda r: [h.vector_score for h in r.vector]}
        for r in rerankers:
            sources[f"{r.name} 重排分"] = lambda run, n=r.name: [h.rerank_score for h in run.reranked[n]["vector"]]
        for name, getter in sources.items():
            pos, neg = top1_scores(questions, runs, getter)
            separation[variant][name] = {
                "auc": auc(pos, neg), "pos_median": statistics.median(pos), "pos_p10": quantile(pos, 0.1),
                "neg_median": statistics.median(neg), "neg_max": max(neg),
            }
    timing.flush()

    def fmt(v, unit):
        return "—" if v is None else f"{v:.2f}{unit}"

    timing_rows = [
        ["query 向量化（智谱 embedding-3）单次耗时中位数", fmt(timing.median("embed_query"), " s")],
        ["云端重排（50 个候选）单次耗时中位数", fmt(timing.median("rerank_zhipu"), " s")],
        ["云端重排单次消耗 token（均值）", fmt(timing.mean_tokens("rerank_zhipu"), "")],
    ]
    for r in rerankers:
        if r.name.startswith("local:"):
            timing_rows.append([f"本地重排 {r.name}（50 个候选）单次耗时中位数", fmt(timing.median(f"rerank_{r.name}"), " s")])
    timing_rows.append(["说明", "耗时只统计真实发出的请求；命中磁盘缓存的不计入。本地重排耗时与机器配置强相关。"])

    primary = "raw"
    answerable = [q for q in questions if is_answerable(q)]
    best_key = max((k for k, _ in schemes), key=lambda k: summarize(questions, results[primary][k], is_answerable)["hit3"])
    from knowledge import KIMI_CORPUS_BUDGET, LONG_DOC_CHARS

    kimi = {
        "long_doc": LONG_DOC_CHARS, "budget": KIMI_CORPUS_BUDGET,
        "short_total": sum(len(t) for t in raw_corpus.values() if len(t) < LONG_DOC_CHARS),
    }
    ctx = {
        "kimi": kimi,
        "questions": questions, "schemes": schemes, "variants": variants_info, "results": results,
        "thresholds": thresholds, "separation": separation, "timing": timing_rows, "best_key": best_key,
        "noise_examples": sorted(noise, key=len, reverse=True)[:6] or ["（未检测到）"],
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "report.md").write_text(build_report(ctx), encoding="utf-8")
    (RESULTS / "results.json").write_text(
        json.dumps({"variants": variants_info, "results": results, "thresholds": thresholds,
                    "separation": separation}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"完成：{RESULTS / 'report.md'}（可答题 {len(answerable)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
