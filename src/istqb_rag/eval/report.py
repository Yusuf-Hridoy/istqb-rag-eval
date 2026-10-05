"""Stage 3 of the eval: aggregate scores.csv into summary.json + a console table."""

import csv
import json
import math
import statistics
from pathlib import Path

from istqb_rag.config import Settings, get_settings
from istqb_rag.eval.score import METRIC_KEYS

ERROR_RATE_LIMIT = 0.05
WORST_N = 10


def load_scores(scores_path: Path) -> list[dict]:
    """Read scores.csv; metric columns become floats (NaN when empty)."""
    if not scores_path.exists():
        return []
    with scores_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for key in METRIC_KEYS + ["best_score"]:
            raw = row.get(key, "")
            row[key] = float(raw) if raw not in ("", None) else math.nan
        row["latency_ms"] = int(row["latency_ms"]) if row["latency_ms"] else 0
        row["multi_chunk"] = row["multi_chunk"] == "true"
    return rows


def _metric_stats(rows: list[dict]) -> dict:
    stats = {}
    for key in METRIC_KEYS:
        values = [r[key] for r in rows if not math.isnan(r[key])]
        nan_count = sum(1 for r in rows if math.isnan(r[key]))
        stats[key] = {
            "mean": round(sum(values) / len(values), 4) if values else None,
            "scored": len(values),
            "nan": nan_count,
        }
    return stats


def _grouped(rows: list[dict], field: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row[field])
        groups.setdefault(key, []).append(row)
    return {key: _metric_stats(group) for key, group in sorted(groups.items())}


def _worst_rows(rows: list[dict], n: int = WORST_N) -> list[dict]:
    def avg(row: dict) -> float:
        values = [row[k] for k in METRIC_KEYS if not math.isnan(row[k])]
        return sum(values) / len(values) if values else math.inf

    def _value(row: dict, key: str):
        return None if math.isnan(row[key]) else row[key]

    in_scope = [r for r in rows if r["type"] == "in_scope" and r["status"] != "error"]
    worst = sorted(in_scope, key=avg)[:n]
    return [
        {
            "id": r["id"],
            "chapter": r["chapter"],
            "k_level": r["k_level"],
            "status": r["status"],
            "avg": round(avg(r), 4),
            "context_precision": _value(r, "context_precision"),
            "context_recall": _value(r, "context_recall"),
            "faithfulness": _value(r, "faithfulness"),
            "response_relevancy": _value(r, "response_relevancy"),
        }
        for r in worst
    ]


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil(pct / 100 * len(ordered)) - 1
    return float(ordered[max(0, min(index, len(ordered) - 1))])


def build_summary(rows: list[dict]) -> dict:
    """Aggregate scores.csv rows into the summary structure."""
    in_scope = [r for r in rows if r["type"] == "in_scope"]
    answered = [r for r in in_scope if r["status"] == "answered"]
    not_answered = [r for r in in_scope if r["status"] in ("refused", "no_context")]
    errors = [r for r in rows if r["status"] == "error"]

    out_scope = [r for r in rows if r["type"] == "out_of_scope"]
    not_in = [r for r in rows if r["type"] == "not_in_syllabus"]
    latencies = [r["latency_ms"] for r in rows]

    error_rate = len(errors) / len(rows) if rows else 0.0
    return {
        "overall": _metric_stats(rows),
        "by_chapter": _grouped(in_scope, "chapter"),
        "by_k_level": _grouped(in_scope, "k_level"),
        "by_multi_chunk": _grouped(in_scope, "multi_chunk"),
        "in_scope_answer_rate": {
            "rate": round(len(answered) / len(in_scope), 4) if in_scope else None,
            "answered": len(answered),
            "total": len(in_scope),
            "not_answered_ids": [r["id"] for r in not_answered],
        },
        "scope_handling": {
            "out_of_scope_accuracy": round(
                sum(1 for r in out_scope if r["status"] in ("refused", "no_context"))
                / len(out_scope),
                4,
            )
            if out_scope
            else None,
            "out_of_scope_total": len(out_scope),
            "not_in_syllabus_accuracy": round(
                sum(1 for r in not_in if r["status"] in ("refused", "no_context")) / len(not_in), 4
            )
            if not_in
            else None,
            "not_in_syllabus_total": len(not_in),
            "possible_hallucination_ids": [r["id"] for r in not_in if r["status"] == "answered"],
        },
        "errors": {
            "count": len(errors),
            "ids": [r["id"] for r in errors],
            "error_rate": round(error_rate, 4),
            "error_rate_ok": error_rate <= ERROR_RATE_LIMIT,
        },
        "worst_10": _worst_rows(rows),
        "latency_ms": {
            "median": statistics.median(latencies) if latencies else 0,
            "p95": _percentile(latencies, 95),
        },
    }


def _print_table(summary: dict) -> None:
    print("\n=== Eval summary ===")
    for key, stats in summary["overall"].items():
        mean = f"{stats['mean']:.3f}" if stats["mean"] is not None else "-"
        print(f"{key:20s} mean={mean}  scored={stats['scored']}  NaN={stats['nan']}")
    rate = summary["in_scope_answer_rate"]
    print(
        f"in-scope answer rate: {rate['answered']}/{rate['total']}"
        f" ({(rate['rate'] or 0) * 100:.1f}%)"
    )
    scope = summary["scope_handling"]
    print(f"out-of-scope accuracy: {scope['out_of_scope_accuracy']}")
    print(f"not-in-syllabus accuracy: {scope['not_in_syllabus_accuracy']}")
    errors = summary["errors"]
    print(
        f"errors: {errors['count']} (rate {errors['error_rate'] * 100:.1f}%,"
        f" {'OK' if errors['error_rate_ok'] else 'RUN FAILED'})"
    )
    lat = summary["latency_ms"]
    print(f"latency: median {lat['median']:.0f} ms, p95 {lat['p95']:.0f} ms")


def run_report(run_id: str, *, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    run_dir = settings.runs_dir / run_id
    rows = load_scores(run_dir / "scores.csv")
    summary = build_summary(rows)
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _print_table(summary)
    print(f"\nwrote {summary_path}")
    return summary
