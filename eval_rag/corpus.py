"""评测语料：按线上完全相同的加载逻辑读 wiki/ 与 data/，并提供去水印版本。

直接复用 backend/knowledge.py 的 KnowledgeBase，保证评测的文档集合和线上
`autoload()` 一致（wiki 摘要 + 能抽出文字的 PDF；扫描件抽不出字，线上也是空的）。
读 22MB 的学生手册 PDF 要二十多秒，所以结果缓存成 JSON。
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # smart_campus/
BACKEND = ROOT / "backend"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
OCR_GLOB = str(ROOT / "wiki" / "_raw_extract" / "*.ocr.txt")

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from eval_rag.metrics import find_noise_lines, strip_noise  # noqa: E402


def load_production_corpus(refresh: bool = False, cache_name: str = "corpus") -> dict[str, str]:
    """{文档名: 全文}，只含全局归属（预置 PDF 与 wiki），与线上新租户看到的一致。

    cache_name：缓存文件名。"corpus" 是接入 OCR 回退之前的快照，评测报告用的就是它；
    线上回归用 "corpus_after" 单独存放接入之后的版本，两者互不覆盖。
    """
    cache = CACHE_DIR / f"{cache_name}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))

    from knowledge import KnowledgeBase
    from tenancy import GLOBAL_TENANT

    kb = KnowledgeBase()
    kb.autoload()
    corpus = {name: text for (owner, name), text in kb._texts.items() if owner == GLOBAL_TENANT}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    return corpus


def ocr_sidecar_docs() -> set[str]:
    """靠 data/ocr/ 旁挂文本才有内容的 PDF 的文档名（线上登记时用的就是 PDF 名）。"""
    return {Path(p).stem + ".pdf" for p in glob.glob(str(ROOT / "data" / "ocr" / "*.txt"))}


def ocr_extra_docs() -> dict[str, str]:
    """本机 OCR 过、但没有随代码发布的扫描件：{PDF 文档名: OCR 全文}。只用于做对照实验。

    来源是 wiki/_raw_extract/_ocr_meta.json（本地缓存，不进 git）；没有这个文件就返回空。
    """
    meta_path = ROOT / "wiki" / "_raw_extract" / "_ocr_meta.json"
    if not meta_path.exists():
        return {}
    shipped = ocr_sidecar_docs()
    out = {}
    for item in json.loads(meta_path.read_text(encoding="utf-8")):
        path = Path(item["extract"])
        if item["name"] not in shipped and path.exists():
            out[item["name"]] = path.read_text(encoding="utf-8")
    return out


def load_ocr_texts() -> dict[str, str]:
    """线上没有索引的 OCR 原文（扫描件抽不出文字层），只用来校验 ocr_gap 题。"""
    return {
        Path(path).name: Path(path).read_text(encoding="utf-8")
        for path in sorted(glob.glob(OCR_GLOB))
    }


def clean_corpus(corpus: dict[str, str]) -> tuple[dict[str, str], set[str]]:
    """去掉每份文档里的水印行；返回（清洗后的语料，所有噪声行的并集）。

    wiki 的 markdown 不会有重复短行，原样保留。
    """
    cleaned: dict[str, str] = {}
    all_noise: set[str] = set()
    for name, text in corpus.items():
        noise = find_noise_lines(text)
        all_noise |= noise
        cleaned[name] = strip_noise(text, noise) if noise else text
    return cleaned, all_noise
