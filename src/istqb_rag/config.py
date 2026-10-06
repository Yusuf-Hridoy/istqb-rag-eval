"""Runtime configuration, loaded from environment variables / .env."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _str(name: str, default: str) -> str:
    return os.getenv(name) or default


def _path(name: str, default: str) -> Path:
    return (_REPO_ROOT / _str(name, default)).resolve()


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    answer_model: str
    embed_model: str
    top_k: int
    min_relevance: float
    chunk_size: int
    chunk_overlap: int
    syllabus_path: Path
    first_content_page: int
    last_content_page: int
    chroma_dir: Path
    collection_name: str
    judge_model: str
    judge_max_tokens: int
    golden_path: Path
    runs_dir: Path


def get_settings() -> Settings:
    """Build Settings from the environment (with .env loaded).

    FIRST_CONTENT_PAGE / LAST_CONTENT_PAGE defaults (14–63) match the printed
    page numbers of ISTQB_CTFL_Syllabus_v4.0.1.pdf: page 14 starts Chapter 1,
    page 63 is the last content page before Appendix A (which starts on page
    64). Override in .env for a different PDF.
    """
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        answer_model=_str("ANSWER_MODEL", "openai/gpt-oss-120b"),
        embed_model=_str("EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        top_k=_int("TOP_K", 4),
        min_relevance=_float("MIN_RELEVANCE", 0.3),
        chunk_size=_int("CHUNK_SIZE", 1000),
        chunk_overlap=_int("CHUNK_OVERLAP", 150),
        syllabus_path=_path("SYLLABUS_PATH", "data/raw/ctfl_syllabus_v4.pdf"),
        first_content_page=_int("FIRST_CONTENT_PAGE", 14),
        last_content_page=_int("LAST_CONTENT_PAGE", 63),
        chroma_dir=_path("CHROMA_DIR", ".chroma"),
        collection_name=_str("COLLECTION_NAME", "ctfl_v4"),
        judge_model=_str("JUDGE_MODEL", "qwen/qwen3.8-27b"),
        judge_max_tokens=_int("JUDGE_MAX_TOKENS", 950),
        golden_path=_path("GOLDEN_PATH", "data/golden_dataset.jsonl"),
        runs_dir=_path("RUNS_DIR", "runs"),
    )
