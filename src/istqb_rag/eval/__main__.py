"""Eval CLI: generate, score, report — or all three in order.

uv run python -m istqb_rag.eval generate --run-id baseline
uv run python -m istqb_rag.eval score    --run-id baseline
uv run python -m istqb_rag.eval report   --run-id baseline
uv run python -m istqb_rag.eval all      --run-id baseline
"""

import argparse
import sys

from istqb_rag.eval import generate as generate_mod
from istqb_rag.eval import report as report_mod
from istqb_rag.eval import score as score_mod
from istqb_rag.eval.dataset import DatasetError, load_golden

MIN_REVIEWED_ROWS = 60


def main() -> None:
    parser = argparse.ArgumentParser(prog="istqb_rag.eval")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "score", "report", "all"):
        p = sub.add_parser(name)
        p.add_argument("--run-id", required=True)
        p.add_argument(
            "--include-unreviewed",
            action="store_true",
            help="dry-run only: also use rows with reviewed: false",
        )
        p.add_argument("--limit", type=int, default=None, help="only use the first N rows")
    args = parser.parse_args()

    try:
        if args.command in ("generate", "all"):
            rows = load_golden(include_unreviewed=args.include_unreviewed, limit=args.limit)
            dry_run = args.include_unreviewed
            if dry_run:
                print(f"DRY RUN on {len(rows)} rows (unreviewed included) — not a baseline.")
            else:
                print(f"Loaded {len(rows)} reviewed rows.")
                if len(rows) < MIN_REVIEWED_ROWS:
                    sys.exit(
                        f"Refusing to run: only {len(rows)} reviewed rows "
                        f"(minimum {MIN_REVIEWED_ROWS}). Review more rows in data/golden.jsonl."
                    )
            from istqb_rag.pipeline import answer

            stats = generate_mod.run_generate(args.run_id, rows, answer, dry_run=dry_run)
            print(f"generate: {stats}")

        if args.command in ("score", "all"):
            rows = load_golden(include_unreviewed=True)
            scorer = score_mod.make_scorer()
            stats = score_mod.run_score(args.run_id, rows, scorer)
            print(f"score: {stats}")

        if args.command in ("report", "all"):
            report_mod.run_report(args.run_id)
    except DatasetError as exc:
        sys.exit(f"Dataset error: {exc}")


if __name__ == "__main__":
    main()
