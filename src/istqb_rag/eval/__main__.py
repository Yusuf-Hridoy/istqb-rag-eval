"""Eval CLI: generate, score, report — or all three in order.

uv run python -m istqb_rag.eval generate --run-id baseline
uv run python -m istqb_rag.eval score    --run-id baseline
uv run python -m istqb_rag.eval report   --run-id baseline
uv run python -m istqb_rag.eval all      --run-id baseline
uv run python -m istqb_rag.eval measure  --run-id baseline   # cost of one row
uv run python -m istqb_rag.eval quick    --run-id exp2       # generate + judge-free report
uv run python -m istqb_rag.eval compare  --base pilot-1 --new exp1
uv run python -m istqb_rag.eval labelsheet --run-id pilot-1  # human labelling sheet
"""

import argparse
import sys

from istqb_rag.eval import step1_ask_questions as generate_mod
from istqb_rag.eval import step2_judge_scores as score_mod
from istqb_rag.eval import step3_build_summary as report_mod
from istqb_rag.eval.dataset import DatasetError, load_golden

MIN_REVIEWED_ROWS = 15  # pilot baseline; the full 75 rows stay in the file


def _load_rows(args) -> list:
    rows = load_golden(include_unreviewed=args.include_unreviewed, limit=args.limit)
    if args.include_unreviewed:
        print(f"DRY RUN on {len(rows)} rows (unreviewed included) — not a baseline.")
        return rows
    print(f"Loaded {len(rows)} reviewed rows.")
    if len(rows) < MIN_REVIEWED_ROWS:
        sys.exit(
            f"Refusing to run: only {len(rows)} reviewed rows "
            f"(minimum {MIN_REVIEWED_ROWS}). Review more rows in data/golden_dataset.jsonl."
        )
    return rows


def _measure(args) -> None:
    """Score one real answered in-scope row and print what it cost."""
    from istqb_rag.config import get_settings
    from istqb_rag.eval.step2_judge_scores import _load_answers, metrics_for

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


def _labelsheet(args) -> None:
    """Write the (gitignored) reading sheet and the empty labels file."""
    from istqb_rag.eval.human_labeling_sheet import write_sheet_and_labels

    rows = load_golden(include_unreviewed=True)
    sheet, labels, n = write_sheet_and_labels(args.run_id, rows)
    print(f"Wrote {sheet} ({n} answered in-scope rows) — gitignored, contains syllabus text.")
    print(f"Wrote {labels} — fill the 'faithful' column with yes/no.")


def _quick(args) -> None:
    """Generate answers, then report everything that needs no judge."""
    from istqb_rag.config import get_settings
    from istqb_rag.eval.compare_runs import reference_pages_from_golden
    from istqb_rag.eval.deterministic_metrics import citation_rate, page_hit_rate
    from istqb_rag.pipeline import answer

    settings = get_settings()
    rows = _load_rows(args)
    stats = generate_mod.run_generate(
        args.run_id, rows, answer, dry_run=args.include_unreviewed, settings=settings
    )
    print(f"generate: {stats}")

    summary = score_mod.write_judge_free_scores(args.run_id, rows, settings=settings)
    scored = summary["rows"]
    print("\n=== Quick report (no judge) ===")
    print(f"rows: {len(scored)}")
    for label, value in summary["status_counts"].items():
        print(f"  status {label:12s} {value}")
    cite = citation_rate(scored)
    print(
        f"citation rate: {cite['rate']} ({cite['cited']}/{cite['answered']} answered rows)"
        + (f" — uncited: {', '.join(cite['uncited_ids'])}" if cite["uncited_ids"] else "")
    )
    hit = page_hit_rate(scored, reference_pages_from_golden(settings.golden_path))
    print(f"page hit rate: {hit['rate']} ({hit['hits']}/{hit['total']})")
    for name, key in (("out_of_scope", "out_of_scope"), ("not_in_syllabus", "not_in_syllabus")):
        group = [r for r in scored if r["type"] == key]
        ok = sum(1 for r in group if r["status"] in ("refused", "no_context"))
        rate = round(ok / len(group), 4) if group else None
        print(f"{name} accuracy: {rate} ({ok}/{len(group)})")
    answered = [r for r in scored if r["type"] == "in_scope" and r["status"] == "answered"]
    in_scope = [r for r in scored if r["type"] == "in_scope"]
    print(f"in-scope answer rate: {len(answered)}/{len(in_scope)}")
    print(f"format fallbacks: {summary['format_fallbacks']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="istqb_rag.eval")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "score", "report", "all", "measure", "quick", "labelsheet"):
        p = sub.add_parser(name)
        p.add_argument("--run-id", required=True)
        p.add_argument(
            "--metrics",
            default=None,
            help="comma-separated judge metrics to score (default: all applicable)",
        )
        p.add_argument(
            "--include-unreviewed",
            action="store_true",
            help="dry-run only: also use rows with reviewed: false",
        )
        p.add_argument("--limit", type=int, default=None, help="only use the first N rows")

    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--base", required=True)
    compare_parser.add_argument("--new", required=True)
    args = parser.parse_args()

    try:
        if args.command == "compare":
            from istqb_rag.config import get_settings
            from istqb_rag.eval.compare_runs import reference_pages_from_golden, run_compare

            settings = get_settings()
            run_compare(
                args.base,
                args.new,
                settings=settings,
                reference_pages=reference_pages_from_golden(settings.golden_path),
            )
            return

        if args.command == "measure":
            _measure(args)
            return

        if args.command == "labelsheet":
            _labelsheet(args)
            return

        if args.command == "quick":
            _quick(args)
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
            only = [m.strip() for m in args.metrics.split(",")] if args.metrics else None
            if only:
                print(f"Scoring only: {', '.join(only)}")
            scorer = score_mod.make_scorer()
            stats = score_mod.run_score(args.run_id, rows, scorer, only_metrics=only)
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
