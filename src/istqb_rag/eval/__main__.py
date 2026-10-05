"""Eval CLI: generate, score, report — or all three in order.

uv run python -m istqb_rag.eval generate --run-id baseline
uv run python -m istqb_rag.eval score    --run-id baseline
uv run python -m istqb_rag.eval report   --run-id baseline
uv run python -m istqb_rag.eval all      --run-id baseline
uv run python -m istqb_rag.eval measure  --run-id baseline   # cost of one row
"""

import argparse
import sys

from istqb_rag.eval import generate as generate_mod
from istqb_rag.eval import report as report_mod
from istqb_rag.eval import score as score_mod
from istqb_rag.eval.dataset import DatasetError, load_golden

MIN_REVIEWED_ROWS = 60


def _load_rows(args) -> list:
    rows = load_golden(include_unreviewed=args.include_unreviewed, limit=args.limit)
    if args.include_unreviewed:
        print(f"DRY RUN on {len(rows)} rows (unreviewed included) — not a baseline.")
        return rows
    print(f"Loaded {len(rows)} reviewed rows.")
    if len(rows) < MIN_REVIEWED_ROWS:
        sys.exit(
            f"Refusing to run: only {len(rows)} reviewed rows "
            f"(minimum {MIN_REVIEWED_ROWS}). Review more rows in data/golden.jsonl."
        )
    return rows


def _measure(args) -> None:
    """Score one real answered in-scope row and print what it cost."""
    from istqb_rag.config import get_settings
    from istqb_rag.eval.score import _load_answers, metrics_for

    settings = get_settings()
    rows = load_golden(include_unreviewed=True)
    answers = _load_answers(settings.runs_dir / args.run_id / "answers.jsonl")
    target = next(
        (
            r
            for r in rows
            if r.type == "in_scope" and r.id in answers and answers[r.id].status == "answered"
        ),
        None,
    )
    if target is None:
        sys.exit(f"No answered in-scope row found in runs/{args.run_id}/answers.jsonl.")

    result = answers[target.id]
    metrics = metrics_for(target.type, result.status)
    scorer = score_mod.make_scorer()
    print(f"Measuring {target.id} with metrics {metrics} …")
    outcome = scorer(
        target.question,
        result.answer,
        target.reference,
        [c.text for c in result.contexts],
        metrics,
    )
    print(f"\n=== Judge cost for one row ({target.id}) ===")
    print(f"judge calls:       {outcome.calls}")
    print(f"prompt tokens:     {outcome.prompt_tokens}")
    print(f"completion tokens: {outcome.completion_tokens}")
    print(f"scores:            {outcome.values}")
    if outcome.api_error:
        print(f"api error:         {outcome.api_error}")
    print(f"\nAt {outcome.calls} calls/row, a 60-row baseline needs ~{outcome.calls * 60} calls.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="istqb_rag.eval")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "score", "report", "all", "measure"):
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
        if args.command == "measure":
            _measure(args)
            return

        if args.command in ("generate", "all"):
            rows = _load_rows(args)
            from istqb_rag.pipeline import answer

            stats = generate_mod.run_generate(
                args.run_id, rows, answer, dry_run=args.include_unreviewed
            )
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
    except score_mod.JudgeQuotaExhausted as exc:
        sys.exit(f"Stopped on judge quota: {exc}")
    except report_mod.RunInvalid as exc:
        sys.exit(f"Run invalid: {exc}")


if __name__ == "__main__":
    main()
