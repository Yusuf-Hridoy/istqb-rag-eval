"""Eval step 3: add the scores up into summary.json and print a readable table."""

import csv
import json
import math
import statistics
from pathlib import Path

from istqb_rag.config import Settings, get_settings
from istqb_rag.eval.step2_judge_scores import METRIC_KEYS, metrics_for

ERROR_RATE_LIMIT = 0.05
NAN_RATE_LIMIT = 0.10
# A rate alone is meaningless on a small run: with 8 judged rows one unparseable
# verdict is already 12.5%, which is noise, not a failed judge.
MIN_NAN_FAILURES = 2
WORST_N = 10
SHORT_METRIC = {
    "context_precision": "prec",
    "context_recall": "rec",
    "faithfulness": "faith",
    "response_relevancy": "rel",
}
# Below this many rows a group mean says more about the sample than the system.
MIN_GROUP_N = 3


class RunInvalid(RuntimeError):
    """Too many judge outputs failed to parse for the run to mean anything."""


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
        row["judge_truncated"] = row.get("judge_truncated", "") == "true"
    return rows


def _expects(row: dict, key: str) -> bool:
    return key in metrics_for(row["type"], row["status"])


def _metric_stats(rows: list[dict], measured: list[str] | None = None) -> dict:
    """Means and NaN counts per metric, counted only for rows the routing table expects."""
    stats = {}
    for key in METRIC_KEYS:
        if measured is not None and key not in measured:
            # Never requested, so it is absent rather than failed, and the validity
            # guard must ignore it.
            stats[key] = {
                "mean": None,
                "scored": 0,
                "expected": 0,
                "nan": 0,
                "nan_truncated": 0,
                "nan_parse_failure": 0,
                "nan_rate": 0.0,
                "parse_failure_rate": 0.0,
                "unmeasured": True,
            }
            continue
        expected = [r for r in rows if _expects(r, key)]
        values = [r[key] for r in expected if not math.isnan(r.get(key, math.nan))]
        missing = [r for r in expected if math.isnan(r.get(key, math.nan))]
        # A verdict JUDGE_MAX_TOKENS cut short is our own configuration, not a parse
        # failure; only unexplained unreadable verdicts feed the validity guard.
        truncated = [r for r in missing if r.get("judge_truncated")]
        parse_failed = [r for r in missing if not r.get("judge_truncated")]
        stats[key] = {
            "mean": round(sum(values) / len(values), 4) if values else None,
            "scored": len(values),
            "expected": len(expected),
            "nan": len(missing),
            "nan_truncated": len(truncated),
            "nan_parse_failure": len(parse_failed),
            "nan_rate": round(len(missing) / len(expected), 4) if expected else 0.0,
            "parse_failure_rate": (
                round(len(parse_failed) / len(expected), 4) if expected else 0.0
            ),
            "unmeasured": False,
        }
    return stats


def _grouped(rows: list[dict], field: str, measured: list[str] | None = None) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row[field])
        groups.setdefault(key, []).append(row)
    out = {}
    for key, group in sorted(groups.items()):
        stats = _metric_stats(group, measured)
        stats["n"] = len(group)
        stats["n_too_small"] = len(group) < MIN_GROUP_N
        out[key] = stats
    return out


def _worst_rows(rows: list[dict], n: int = WORST_N) -> list[dict]:
    def avg(row: dict) -> float:
        values = [row[k] for k in METRIC_KEYS if not math.isnan(row.get(k, math.nan))]
        return sum(values) / len(values) if values else math.inf

    def _value(row: dict, key: str):
        return None if math.isnan(row.get(key, math.nan)) else row[key]

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


def build_summary(rows: list[dict], measured: list[str] | None = None) -> dict:
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
        "overall": _metric_stats(rows, measured),
        "by_chapter": _grouped(in_scope, "chapter", measured),
        "by_k_level": _grouped(in_scope, "k_level", measured),
        "by_multi_chunk": _grouped(in_scope, "multi_chunk", measured),
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
        extra = ""
        if stats["nan"]:
            extra = (
                f" (parse-fail {stats['nan_parse_failure']}, truncated {stats['nan_truncated']})"
            )
        print(f"{key:20s} mean={mean}  scored={stats['scored']}  NaN={stats['nan']}{extra}")
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
    for section, title in (
        ("by_chapter", "chapter"),
        ("by_k_level", "K-level"),
        ("by_multi_chunk", "multi_chunk"),
    ):
        groups = summary.get(section, {})
        if not groups:
            continue
        print(f"\n-- by {title} --")
        for name, stats in groups.items():
            cells = []
            for key in METRIC_KEYS:
                mean = stats[key]["mean"]
                label = SHORT_METRIC[key]
                cells.append(f"{label}={mean:.2f}" if mean is not None else f"{label}=-")
            flag = "  (n too small)" if stats["n_too_small"] else ""
            print(f"  {name:<12} n={stats['n']:<3} " + "  ".join(cells) + flag)

    lat = summary["latency_ms"]
    print(f"\nlatency: median {lat['median']:.0f} ms, p95 {lat['p95']:.0f} ms")


def failed_nan_metrics(summary: dict, limit: float = NAN_RATE_LIMIT) -> list[tuple[str, float]]:
    """Metrics whose parse failures are both above ``limit`` and at least MIN_NAN_FAILURES."""
    return [
        (key, stats["parse_failure_rate"])
        for key, stats in summary["overall"].items()
        if stats["expected"]
        and stats["parse_failure_rate"] > limit
        and stats["nan_parse_failure"] >= MIN_NAN_FAILURES
    ]


def run_report(run_id: str, *, settings: Settings | None = None) -> dict:
    """Aggregate a run into summary.json, or raise RunInvalid and write nothing."""
    settings = settings or get_settings()
    run_dir = settings.runs_dir / run_id
    rows = load_scores(run_dir / "scores.csv")
    measured = None
    config_path = run_dir / "config.json"
    if config_path.exists():
        measured = json.loads(config_path.read_text(encoding="utf-8")).get("metrics_scored")
    summary = build_summary(rows, measured)
    _print_table(summary)

    bad = failed_nan_metrics(summary)
    if bad:
        detail = ", ".join(f"{k} {rate * 100:.1f}%" for k, rate in bad)
        raise RunInvalid(
            f"judge output failed to parse above {NAN_RATE_LIMIT * 100:.0f}% for: {detail}. "
            "summary.json was not written — this run is not a valid baseline."
        )

    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    from istqb_rag.eval.plain_report import write_report

    report_path = write_report(run_dir, summary, rows, settings.golden_path)
    print(f"\nwrote {summary_path}")
    print(f"wrote {report_path}")
    return summary
