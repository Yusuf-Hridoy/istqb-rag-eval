"""Fail the build if a published result could be wrong or a file could leak text.

CI has no syllabus PDF and no API keys, so it cannot run the bot or reproduce a
score. What it *can* do is guarantee that the numbers in the README were
generated from the run files rather than typed, that every run is complete, that
no committed file has a column able to hold syllabus or answer text, and that
the dataset still validates. That is what this checks.

Run: uv run python scripts/check_results_integrity.py
"""

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from istqb_rag.eval.dataset import DatasetError, load_golden  # noqa: E402
from istqb_rag.eval.readme_tables import (  # noqa: E402
    apply_tables,
    build_tables,
    missing_markers,
)
from istqb_rag.eval.step2_judge_scores import SCORES_COLUMNS  # noqa: E402

REQUIRED_RUN_FILES = ("config.json", "scores.csv", "summary.json")
# Only these may appear in a committed scores.csv. Anything else could carry a
# question, an answer or syllabus text into git.
ALLOWED_SCORE_COLUMNS = set(SCORES_COLUMNS)


def check_readme_tables(problems: list[str]) -> None:
    readme = REPO_ROOT / "README.md"
    if not readme.exists():
        problems.append("README.md is missing")
        return
    original = readme.read_text(encoding="utf-8")
    tables = build_tables()

    absent = missing_markers(original, tables)
    if absent:
        problems.append(
            "README.md is missing result markers for: "
            + ", ".join(absent)
            + " (expected <!-- results:<name>:start --> / :end -->)"
        )
        return

    regenerated = apply_tables(original, tables)
    if regenerated != original:
        problems.append(
            "README.md results tables do not match the run files. "
            "They are generated, not hand-written — run:\n"
            "    uv run python -m istqb_rag.eval readme-tables"
        )


def check_runs_complete(problems: list[str]) -> None:
    runs_dir = REPO_ROOT / "runs"
    if not runs_dir.exists():
        return
    for run in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        for name in REQUIRED_RUN_FILES:
            if not (run / name).exists():
                problems.append(f"runs/{run.name}/ is missing {name}")


def check_scores_columns(problems: list[str]) -> None:
    runs_dir = REPO_ROOT / "runs"
    if not runs_dir.exists():
        return
    for scores in sorted(runs_dir.glob("*/scores.csv")):
        with scores.open(newline="", encoding="utf-8") as f:
            header = next(csv.reader(f), [])
        unknown = [c for c in header if c and c not in ALLOWED_SCORE_COLUMNS]
        if unknown:
            problems.append(
                f"runs/{scores.parent.name}/scores.csv has non-allowlisted column(s): "
                f"{', '.join(unknown)} — committed scores must never hold question, "
                "answer or syllabus text"
            )


def check_golden_dataset(problems: list[str]) -> None:
    path = REPO_ROOT / "data" / "golden_dataset.jsonl"
    try:
        rows = load_golden(path, include_unreviewed=True)
    except DatasetError as exc:
        problems.append(f"data/golden_dataset.jsonl failed validation: {exc}")
        return
    unattributed = [r.id for r in rows if r.reviewed and not r.reviewed_by]
    if unattributed:
        problems.append(
            "rows marked reviewed: true with no reviewed_by: " + ", ".join(unattributed)
        )


def main() -> int:
    problems: list[str] = []
    check_readme_tables(problems)
    check_runs_complete(problems)
    check_scores_columns(problems)
    check_golden_dataset(problems)

    if problems:
        print("Results integrity check FAILED:\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Results integrity check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
