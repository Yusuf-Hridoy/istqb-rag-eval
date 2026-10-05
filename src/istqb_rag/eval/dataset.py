"""Golden dataset loading and validation.

data/golden.jsonl holds one JSON object per line: questions and short
paraphrased reference answers only — never copied syllabus passages.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from istqb_rag.config import Settings, get_settings

RowType = Literal["in_scope", "not_in_syllabus", "out_of_scope"]

ALLOWED_TYPES = {"in_scope", "not_in_syllabus", "out_of_scope"}
ALLOWED_K_LEVELS = {"K1", "K2", "K3"}
ALLOWED_SOURCES = {"llm", "human"}
REQUIRED_KEYS = {
    "id",
    "question",
    "reference",
    "reference_pages",
    "chapter",
    "section",
    "k_level",
    "type",
    "multi_chunk",
    "source",
    "reviewed",
}


class DatasetError(ValueError):
    """Raised when data/golden.jsonl fails validation."""


@dataclass(frozen=True)
class GoldenRow:
    id: str
    question: str
    reference: str | None
    reference_pages: list[int]
    chapter: int | None
    section: str | None
    k_level: str | None
    type: RowType
    multi_chunk: bool
    source: str
    reviewed: bool


def _fail(lineno: int, msg: str) -> DatasetError:
    return DatasetError(f"golden.jsonl line {lineno}: {msg}")


def _validate_row(raw: object, lineno: int) -> GoldenRow:
    if not isinstance(raw, dict):
        raise _fail(lineno, "row is not a JSON object")
    missing = REQUIRED_KEYS - raw.keys()
    if missing:
        raise _fail(lineno, f"missing keys: {sorted(missing)}")

    row_id = raw["id"]
    if not isinstance(row_id, str) or not row_id.strip():
        raise _fail(lineno, "id must be a non-empty string")
    if not isinstance(raw["question"], str) or not raw["question"].strip():
        raise _fail(lineno, f"{row_id}: question must be a non-empty string")
    if raw["type"] not in ALLOWED_TYPES:
        raise _fail(lineno, f"{row_id}: type must be one of {sorted(ALLOWED_TYPES)}")
    if not isinstance(raw["multi_chunk"], bool):
        raise _fail(lineno, f"{row_id}: multi_chunk must be a boolean")
    if raw["source"] not in ALLOWED_SOURCES:
        raise _fail(lineno, f"{row_id}: source must be one of {sorted(ALLOWED_SOURCES)}")
    if not isinstance(raw["reviewed"], bool):
        raise _fail(lineno, f"{row_id}: reviewed must be a boolean")

    row_type = raw["type"]
    if row_type == "in_scope":
        if not isinstance(raw["reference"], str) or not raw["reference"].strip():
            raise _fail(lineno, f"{row_id}: in_scope row needs a non-empty reference")
        pages = raw["reference_pages"]
        if not isinstance(pages, list) or not pages or not all(isinstance(p, int) for p in pages):
            raise _fail(lineno, f"{row_id}: in_scope row needs reference_pages as ints")
        if not isinstance(raw["chapter"], int):
            raise _fail(lineno, f"{row_id}: in_scope row needs an integer chapter")
        if not isinstance(raw["section"], str) or not raw["section"].strip():
            raise _fail(lineno, f"{row_id}: in_scope row needs a section")
        if raw["k_level"] not in ALLOWED_K_LEVELS:
            raise _fail(lineno, f"{row_id}: k_level must be one of {sorted(ALLOWED_K_LEVELS)}")
        reference = raw["reference"]
        reference_pages = pages
        chapter = raw["chapter"]
        section = raw["section"]
        k_level = raw["k_level"]
    else:
        if raw["reference"] is not None:
            raise _fail(lineno, f"{row_id}: non-in_scope row must have reference: null")
        if raw["reference_pages"] != []:
            raise _fail(lineno, f"{row_id}: non-in_scope row must have reference_pages: []")
        for field in ("chapter", "section", "k_level"):
            if raw[field] is not None:
                raise _fail(lineno, f"{row_id}: non-in_scope row must have {field}: null")
        reference = None
        reference_pages = []
        chapter = None
        section = None
        k_level = None

    return GoldenRow(
        id=row_id,
        question=raw["question"],
        reference=reference,
        reference_pages=reference_pages,
        chapter=chapter,
        section=section,
        k_level=k_level,
        type=row_type,
        multi_chunk=raw["multi_chunk"],
        source=raw["source"],
        reviewed=raw["reviewed"],
    )


def load_golden(
    path: Path | None = None,
    *,
    include_unreviewed: bool = False,
    limit: int | None = None,
    settings: Settings | None = None,
) -> list[GoldenRow]:
    """Load and validate the golden dataset.

    By default only reviewed rows are returned. ``include_unreviewed`` is for
    dry runs only; any run using it must be labelled dry-run and is never
    committed as a baseline.
    """
    settings = settings or get_settings()
    path = path or settings.golden_path
    if not path.exists():
        raise DatasetError(f"golden dataset not found at {path}")

    rows: list[GoldenRow] = []
    seen_ids: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise _fail(lineno, f"invalid JSON: {exc}") from exc
            row = _validate_row(raw, lineno)
            if row.id in seen_ids:
                raise _fail(lineno, f"duplicate id: {row.id}")
            seen_ids.add(row.id)
            rows.append(row)

    if not include_unreviewed:
        rows = [r for r in rows if r.reviewed]
    if limit is not None:
        rows = rows[:limit]
    return rows
