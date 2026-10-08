"""Eval CLI: generate, score and report a run, plus compare, variance and quick."""

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


def _quick(args) -> None:
    from istqb_rag.config import get_settings
    from istqb_rag.eval.compare_runs import reference_pages_from_golden
    from istqb_rag.eval.deterministic_metrics import (
        citation_rate,
        citation_validity,
        page_hit_rate,
    )
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
    valid = citation_validity(scored)
    print(
        f"citation validity: {valid['rate']} "
        f"({valid['valid_pages']}/{valid['total_pages']} cited pages were retrieved,"
        f" over {valid['rows']} rows)"
        + (f" — invented in: {', '.join(valid['invalid_ids'])}" if valid["invalid_ids"] else "")
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


def _variance(args) -> None:
    import json as _json
    from pathlib import Path

    from istqb_rag.config import get_settings
    from istqb_rag.eval.answer_variance import (
        apply_decision_rule,
        load_run,
        mode_summary,
        render_markdown,
        write_results,
    )

    settings = get_settings()
    text = mode_summary([load_run(settings.runs_dir / r) for r in args.text])
    structured = mode_summary([load_run(settings.runs_dir / r) for r in args.structured])
    decision = apply_decision_rule(text, structured)

    notes = {}
    if args.notes:
        notes = {k: v for k, v in _json.loads(Path(args.notes).read_text()).items()}

    doc = settings.golden_path.parent.parent / "docs" / "answer-variance.md"
    write_results(doc, render_markdown(text, structured, decision, notes))

    print("=== Decision rule ===")
    for cond in decision["conditions"]:
        print(
            f"  ({cond['id']}) {'MET    ' if cond['met'] else 'NOT MET'} "
            f"{cond['text']} — {cond['evidence']}"
        )
    print(f"\n  => ANSWER_FORMAT default: {decision['default']}")
    if decision["failed"]:
        print(f"  => failed condition(s): {', '.join(decision['failed'])}")
    for name, mode in (("text", text), ("structured", structured)):
        flips = mode["status_flips"]
        print(f"\n{name}: citation rate {mode['rates']['citation_rate']}")
        print(f"{name}: status flips -> {flips or 'none'}")
        print(f"{name}: identical answers on {mode['identical_rows']}/{mode['measured_rows']} rows")
    print(f"\nwrote {doc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="istqb_rag.eval")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "score", "report", "all", "quick"):
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

    sub.add_parser("readme-tables")

    variance_parser = sub.add_parser("variance")
    variance_parser.add_argument("--text", nargs="+", required=True, help="text-mode run ids")
    variance_parser.add_argument(
        "--structured", nargs="+", required=True, help="structured-mode run ids"
    )
    variance_parser.add_argument(
        "--notes", default=None, help="JSON file: {row_id: reading} for scope rows answered"
    )

    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--base", required=True)
    compare_parser.add_argument("--new", required=True)
    args = parser.parse_args()

    try:
        if args.command == "readme-tables":
            from pathlib import Path as _Path

            from istqb_rag.eval.readme_tables import write_readme_tables

            readme = _Path(__file__).resolve().parents[3] / "README.md"
            changed = write_readme_tables(readme)
            print(f"{'updated' if changed else 'already up to date'}: {readme}")
            return

        if args.command == "variance":
            _variance(args)
            return

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
                unknown = [m for m in only if m not in score_mod.METRIC_KEYS]
                if unknown:
                    sys.exit(
                        f"Unknown metric(s): {', '.join(unknown)}. "
                        f"Choose from: {', '.join(score_mod.METRIC_KEYS)}."
                    )
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
