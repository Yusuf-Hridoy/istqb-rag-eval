"""Eval step 1: ask the bot every question in the dataset and save its answers.

Each RagResult is appended to the run's answers.jsonl as soon as it returns;
rerunning skips rows already present, so a crash or rate limit never loses
work. answers.jsonl is gitignored (it contains syllabus text in contexts).
"""

import dataclasses
import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from istqb_rag.config import Settings, get_settings
from istqb_rag.eval.dataset import GoldenRow
from istqb_rag.result_types import RagResult

AnswerFn = Callable[[str], RagResult]


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return ""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_run_config(
    run_dir: Path,
    run_id: str,
    row_count: int,
    *,
    dry_run: bool,
    settings: Settings,
) -> Path:
    """Write runs/<run_id>/config.json (committed; no question or answer text)."""
    import ragas

    config = {
        "run_id": run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "golden_sha256": _sha256(settings.golden_path) if settings.golden_path.exists() else "",
        "row_count": row_count,
        "answer_model": settings.answer_model,
        "judge_model": settings.judge_model,
        "embedding_model": settings.embed_model,
        "top_k": settings.top_k,
        "min_relevance": settings.min_relevance,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "ragas_version": ragas.__version__,
        "dry_run": dry_run,
    }
    path = run_dir / "config.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def _existing_ids(answers_path: Path) -> set[str]:
    if not answers_path.exists():
        return set()
    with answers_path.open(encoding="utf-8") as f:
        return {json.loads(line)["id"] for line in f if line.strip()}


def run_generate(
    run_id: str,
    rows: list[GoldenRow],
    answer_fn: AnswerFn,
    *,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Answer every row, appending each result to answers.jsonl immediately."""
    settings = settings or get_settings()
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_run_config(run_dir, run_id, len(rows), dry_run=dry_run, settings=settings)

    answers_path = run_dir / "answers.jsonl"
    done = _existing_ids(answers_path)
    generated = skipped = 0
    with answers_path.open("a", encoding="utf-8") as f:
        for i, row in enumerate(rows, 1):
            if row.id in done:
                skipped += 1
                continue
            result = answer_fn(row.question)
            record = {"id": row.id, "result": dataclasses.asdict(result)}
            f.write(json.dumps(record) + "\n")
            f.flush()
            generated += 1
            print(f"[{i}/{len(rows)}] {row.id}: {result.status} ({generated} generated)")
    return {"generated": generated, "skipped": skipped}
