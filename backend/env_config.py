"""统一加载 backend/.env，不依赖进程当前工作目录。"""
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parent
_ENV_LOADED = False


def backend_dir() -> Path:
    return _BACKEND_DIR


def load_backend_env() -> Path:
    """加载与本文件同目录下的 .env；重复调用安全。"""
    global _ENV_LOADED
    env_path = _BACKEND_DIR / ".env"
    if not _ENV_LOADED:
        load_dotenv(env_path)
        _ENV_LOADED = True
    return env_path


def chroma_persist_dir() -> str:
    return str(_BACKEND_DIR / "chroma_db_data")
