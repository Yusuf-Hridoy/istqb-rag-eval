"""Build the README's results tables from the run files, so no number is hand-typed.

Each table lives in the README between a pair of markers. `eval readme-tables`
rewrites the blocks; `scripts/check_results_integrity.py` regenerates them in
memory and fails CI if the file differs. A number edited by hand therefore
cannot survive a pull request.
"""

import json
from pathlib import Path

from istqb_rag.config import Settings, get_settings
from istqb_rag.eval.answer_variance import apply_decision_rule, load_run, mode_summary
from istqb_rag.eval.compare_runs import reference_pages_from_golden
from istqb_rag.eval.deterministic_metrics import (
    backfill_retrieved_pages,
    citation_rate,
    citation_validity,
    page_hit_rate,
)
from istqb_rag.eval.step3_build_summary import load_scores

BASELINE_RUN = "pilot-1"
EXPERIMENT_RUNS = [
    ("pilot-1", "baseline"),
    ("pilot-1-repeat", "control, same config"),
    ("exp1-section-chunking", "section chunking"),
    ("exp2-structured-answers", "structured answers"),
]
TEXT_RUNS = ["pilot-1", "pilot-1-repeat", "text-repeat-3", "text-repeat-4"]
STRUCTURED_RUNS = [
    "exp2-structured-answers",
    "structured-repeat-2",
    "structured-repeat-3",
    "structured-repeat-4",
]

METRIC_LABELS = {
    "context_precision": "Context precision",
    "context_recall": "Context recall",
    "faithfulness": "Faithfulness",
    "response_relevancy": "Response relevancy",
}


def _rows(settings: Settings, run_id: str) -> list[dict]:
    run_dir = settings.runs_dir / run_id
    return backfill_retrieved_pages(load_scores(run_dir / "scores.csv"), run_dir / "answers.jsonl")


def baseline_table(settings: Settings) -> str:
    """The judged pilot baseline: the four Ragas metrics with their n."""
    summary = json.loads(
        (settings.runs_dir / BASELINE_RUN / "summary.json").read_text(encoding="utf-8")
    )
    lines = [
        f"Run `{BASELINE_RUN}` — 15 rows, judged by `qwen/qwen3.8-27b`.",
        "",
        "| Metric | Mean | Rows scored (n) | NaN (parse / truncated) |",
        "|---|---|---|---|",
    ]
    for key, label in METRIC_LABELS.items():
        stats = summary["overall"][key]
        mean = "—" if stats["mean"] is None else f"{stats['mean']:.3f}"
        lines.append(
            f"| {label} | {mean} | {stats['scored']} "
            f"| {stats['nan_parse_failure']} / {stats['nan_truncated']} |"
        )
    rate = summary["in_scope_answer_rate"]
    lines += [
        "",
        f"In-scope answer rate {rate['rate']:.3f} ({rate['answered']}/{rate['total']}) · "
        f"errors {summary['errors']['count']} · "
        f"latency median {summary['latency_ms']['median']:.0f} ms, "
        f"p95 {summary['latency_ms']['p95']:.0f} ms.",
    ]
    return "\n".join(lines)


def experiments_table(settings: Settings) -> str:
    """Deterministic rates plus context recall, across the four compared runs."""
    reference = reference_pages_from_golden(settings.golden_path)
    data = {}
    for run_id, _ in EXPERIMENT_RUNS:
        rows = _rows(settings, run_id)
        summary = json.loads(
            (settings.runs_dir / run_id / "summary.json").read_text(encoding="utf-8")
        )
        recall = summary["overall"]["context_recall"]
        out_scope = [r for r in rows if r["type"] == "out_of_scope"]
        data[run_id] = {
            "recall": None if recall.get("unmeasured") else recall["mean"],
            "recall_n": recall["scored"],
            "page_hit": page_hit_rate(rows, reference),
            "citation": citation_rate(rows),
            "validity": citation_validity(rows),
            "oos": (
                sum(1 for r in out_scope if r["status"] in ("refused", "no_context"))
                / len(out_scope)
                if out_scope
                else None
            ),
            "oos_n": len(out_scope),
        }

    header = "| metric | " + " | ".join(f"{label}<br>`{run}`" for run, label in EXPERIMENT_RUNS)
    lines = [header + " |", "|---" * (len(EXPERIMENT_RUNS) + 1) + "|"]

    def row(label, fn):
        return f"| {label} | " + " | ".join(fn(data[run]) for run, _ in EXPERIMENT_RUNS) + " |"

    lines.append(
        row(
            "Context recall",
            lambda d: (
                "not re-judged" if d["recall"] is None else f"{d['recall']:.3f} (n={d['recall_n']})"
            ),
        )
    )
    lines.append(
        row("Page hit rate", lambda d: f"{d['page_hit']['rate']:.3f} (n={d['page_hit']['total']})")
    )
    lines.append(
        row(
            "Citation rate",
            lambda d: f"{d['citation']['rate']:.3f} (n={d['citation']['answered']})",
        )
    )
    lines.append(
        row(
            "Citation validity",
            lambda d: f"{d['validity']['rate']:.3f} (n={d['validity']['rows']})",
        )
    )
    lines.append(row("Out-of-scope accuracy", lambda d: f"{d['oos']:.3f} (n={d['oos_n']})"))
    return "\n".join(lines)


def variance_table(settings: Settings) -> str:
    """Spread across four runs of each answer format, and the decision outcome."""
    text = mode_summary([load_run(settings.runs_dir / r) for r in TEXT_RUNS])
    structured = mode_summary([load_run(settings.runs_dir / r) for r in STRUCTURED_RUNS])
    decision = apply_decision_rule(text, structured)

    def cell(mode, key):
        s = mode["rates"][key]
        return "—" if s["mean"] is None else f"{s['mean']:.3f} ({s['min']:.3f}–{s['max']:.3f})"

    lines = [
        "Mean across 4 runs of each mode, with (min–max).",
        "",
        "| metric | text (4 runs) | structured (4 runs) |",
        "|---|---|---|",
    ]
    for label, key in (
        ("Citation rate", "citation_rate"),
        ("Out-of-scope accuracy", "out_of_scope_accuracy"),
        ("Not-in-syllabus accuracy", "not_in_syllabus_accuracy"),
        ("Answer rate", "answer_rate"),
    ):
        lines.append(f"| {label} | {cell(text, key)} | {cell(structured, key)} |")
    lines.append(
        f"| Format fallbacks (total) | {text['total_fallbacks']} "
        f"| {structured['total_fallbacks']} |"
    )
    lines.append(
        f"| Rows with an identical answer every run | "
        f"{text['identical_rows']} of {text['measured_rows']} | "
        f"{structured['identical_rows']} of {structured['measured_rows']} |"
    )
    lines += [
        "",
        f"Status flips across runs — text: "
        f"{', '.join(f'`{k}`' for k in text['status_flips']) or 'none'}; "
        f"structured: {', '.join(f'`{k}`' for k in structured['status_flips']) or 'none'}.",
        "",
        f"Decision rule (written before the runs): **default = `{decision['default']}`**"
        + (
            ""
            if not decision["failed"]
            else f", failed condition(s) {', '.join(decision['failed'])}"
        )
        + ".",
    ]
    return "\n".join(lines)


def build_tables(settings: Settings | None = None) -> dict[str, str]:
    settings = settings or get_settings()
    return {
        "baseline": baseline_table(settings),
        "experiments": experiments_table(settings),
        "variance": variance_table(settings),
    }


def marker(name: str, end: bool = False) -> str:
    return f"<!-- results:{name}:{'end' if end else 'start'} -->"


def apply_tables(readme: str, tables: dict[str, str]) -> str:
    """Replace each marked block. A missing marker pair is left alone."""
    for name, body in tables.items():
        start, end = marker(name), marker(name, end=True)
        if start not in readme or end not in readme:
            continue
        head, _, rest = readme.partition(start)
        _, _, tail = rest.partition(end)
        readme = f"{head}{start}\n{body}\n{end}{tail}"
    return readme


def missing_markers(readme: str, tables: dict[str, str]) -> list[str]:
    return [
        name
        for name in tables
        if marker(name) not in readme or marker(name, end=True) not in readme
    ]


def write_readme_tables(readme_path: Path, settings: Settings | None = None) -> bool:
    """Rewrite the README's generated blocks. True when the file changed."""
    tables = build_tables(settings)
    original = readme_path.read_text(encoding="utf-8")
    updated = apply_tables(original, tables)
    if updated != original:
        readme_path.write_text(updated, encoding="utf-8")
    return updated != original
