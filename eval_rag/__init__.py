"""RAG 评测与线上回归。评测直接 import backend/ 里的线上代码，所以先把它放进模块搜索路径。"""
import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parent.parent / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
