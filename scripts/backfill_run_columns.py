"""Bring an older run's scores.csv up to the current column schema.

Runs written before a column existed are missing it, which used to be papered
over by reading the gitignored `answers.jsonl` at report time. That worked
locally and failed on a clean checkout, where the file does not exist. The fix
is to persist the values once, here, so every published number comes from a
committed file.

Only ever *adds* missing cells. An existing value is never overwritten, so a run
that already has a column is left exactly as it was.

Run: uv run python scripts/backfill_run_columns.py [--dry-run]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from istqb_rag.eval.step2_judge_scores import SCORES_COLUMNS, answer_digest  # noqa: E402

# Columns this script can reconstruct from a run's answers.jsonl.
DERIVABLE = ("retrieved_pages", "format_fallback", "answer_sha256")


def derive_from_answers(answers_path: Path) -> dict[str, dict[str, str]]:
    """{row id: {column: value}} rebuilt from the run's saved answers."""
    derived: dict[str, dict[str, str]] = {}
    if not answers_path.exists():
        return derived
    for line in answers_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        result = record["result"]
        pages = dict.fromkeys(c["page"] for c in result.get("contexts", []))
        derived[record["id"]] = {
            "retrieved_pages": ";".join(str(p) for p in pages),
            "format_fallback": "true" if result.get("format_fallback") else "",
            "answer_sha256": answer_digest(result.get("answer")),
        }
    return derived


def backfill_run(run_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    scores_path = run_dir / "scores.csv"
    if not scores_path.exists():
        return {"filled": 0, "rows": 0}

    with scores_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_columns = list(reader.fieldnames or [])
        rows = list(reader)

    derived = derive_from_answers(run_dir / "answers.jsonl")
    filled = 0
    for row in rows:
        values = derived.get(row["id"], {})
        for column in DERIVABLE:
            current = (row.get(column) or "").strip()
            if current:
                continue  # never overwrite a value that is already there
            if column in values and values[column] != "":
                row[column] = values[column]
                filled += 1

    missing_columns = [c for c in SCORES_COLUMNS if c not in existing_columns]
    if not filled and not missing_columns:
        return {"filled": 0, "rows": len(rows)}

    if not dry_run:
        with scores_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SCORES_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in SCORES_COLUMNS})
    return {"filled": filled, "rows": len(rows), "added_columns": missing_columns}


def main() -> int:
    parser = argparse.ArgumentParser(prog="backfill_run_columns")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    runs_dir = REPO_ROOT / "runs"
    for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        stats = backfill_run(run_dir, dry_run=args.dry_run)
        added = stats.get("added_columns") or []
        note = f"+{stats['filled']} cells" if stats["filled"] else "nothing to fill"
        if added:
            note += f", added column(s): {', '.join(added)}"
        print(f"  {run_dir.name:26s} {stats['rows']:>3} rows — {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
