"""Compare two runs row by row: metric deltas, status changes, and what moved.

Runs are joined on question id, never on row order, so a run that scored a
different subset still lines up. The output deliberately contains no words like
"improved" — it reports numbers and n, and the findings document does the
interpreting.
"""

import json
import math
from pathlib import Path

from istqb_rag.config import Settings, get_settings
from istqb_rag.eval.deterministic_metrics import (
    citation_rate,
    citation_validity,
    page_hit_rate,
)
from istqb_rag.eval.step2_judge_scores import METRIC_KEYS
from istqb_rag.eval.step3_build_summary import load_scores

# How far a metric must move before a row is worth listing individually.
MOVE_THRESHOLD = 0.2


def _mean(rows: list[dict], key: str) -> tuple[float | None, int]:
    values = [r[key] for r in rows if not math.isnan(r.get(key, math.nan))]
    return (round(sum(values) / len(values), 4) if values else None, len(values))


def _scope_accuracy(rows: list[dict], row_type: str) -> tuple[float | None, int]:
    group = [r for r in rows if r["type"] == row_type]
    if not group:
        return (None, 0)
    ok = sum(1 for r in group if r["status"] in ("refused", "no_context"))
    return (round(ok / len(group), 4), len(group))


def _answer_rate(rows: list[dict]) -> tuple[float | None, int]:
    in_scope = [r for r in rows if r["type"] == "in_scope"]
    if not in_scope:
        return (None, 0)
    answered = sum(1 for r in in_scope if r["status"] == "answered")
    return (round(answered / len(in_scope), 4), len(in_scope))


def compare(
    base_rows: list[dict],
    new_rows: list[dict],
    reference_pages: dict[str, list[int]] | None = None,
) -> dict:
    """Join two runs' scores on id and report every difference."""
    base = {r["id"]: r for r in base_rows}
    new = {r["id"]: r for r in new_rows}
    shared = sorted(set(base) & set(new))

    metrics = {}
    for key in METRIC_KEYS:
        base_mean, base_n = _mean([base[i] for i in shared], key)
        new_mean, new_n = _mean([new[i] for i in shared], key)
        delta = (
            round(new_mean - base_mean, 4)
            if base_mean is not None and new_mean is not None
            else None
        )
        metrics[key] = {
            "base_mean": base_mean,
            "base_n": base_n,
            "new_mean": new_mean,
            "new_n": new_n,
            "delta": delta,
        }

    changed_rows = []
    for row_id in shared:
        before, after = base[row_id], new[row_id]
        moved = {}
        for key in METRIC_KEYS:
            was, now = before.get(key, math.nan), after.get(key, math.nan)
            if math.isnan(was) and math.isnan(now):
                continue
            if math.isnan(was) or math.isnan(now):
                moved[key] = {
                    "base": None if math.isnan(was) else was,
                    "new": None if math.isnan(now) else now,
                    "delta": None,
                }
            elif abs(now - was) > MOVE_THRESHOLD:
                moved[key] = {"base": was, "new": now, "delta": round(now - was, 4)}
        status_changed = before["status"] != after["status"]
        if moved or status_changed:
            changed_rows.append(
                {
                    "id": row_id,
                    "multi_chunk": bool(before.get("multi_chunk")),
                    "base_status": before["status"],
                    "new_status": after["status"],
                    "status_changed": status_changed,
                    "moved": moved,
                }
            )

    direction = {}
    for key in METRIC_KEYS:
        better = worse = same = 0
        for row_id in shared:
            b = base[row_id].get(key, math.nan)
            a = new[row_id].get(key, math.nan)
            if math.isnan(b) or math.isnan(a):
                continue
            if a > b:
                better += 1
            elif a < b:
                worse += 1
            else:
                same += 1
        direction[key] = {"higher": better, "lower": worse, "unchanged": same}

    result = {
        "rows_compared": len(shared),
        "base_only_ids": sorted(set(base) - set(new)),
        "new_only_ids": sorted(set(new) - set(base)),
        "metrics": metrics,
        "citation_rate": {
            "base": citation_rate(base_rows),
            "new": citation_rate(new_rows),
        },
        "citation_validity": {
            "base": citation_validity(base_rows),
            "new": citation_validity(new_rows),
        },
        "out_of_scope_accuracy": {
            "base": _scope_accuracy(base_rows, "out_of_scope"),
            "new": _scope_accuracy(new_rows, "out_of_scope"),
        },
        "not_in_syllabus_accuracy": {
            "base": _scope_accuracy(base_rows, "not_in_syllabus"),
            "new": _scope_accuracy(new_rows, "not_in_syllabus"),
        },
        "answer_rate": {"base": _answer_rate(base_rows), "new": _answer_rate(new_rows)},
        "changed_rows": changed_rows,
        "direction": direction,
    }
    if reference_pages:
        result["page_hit_rate"] = {
            "base": page_hit_rate(base_rows, reference_pages),
            "new": page_hit_rate(new_rows, reference_pages),
        }
    return result


def print_comparison(data: dict, base_id: str, new_id: str) -> None:
    """Numbers and n only — no verdicts; the findings doc interprets."""
    print(f"\n=== {base_id} vs {new_id} ===")
    print(f"rows compared: {data['rows_compared']}")
    print(f"\n{'metric':22s} {'base':>14s} {'new':>14s} {'delta':>8s}")
    for key, m in data["metrics"].items():
        base = f"{m['base_mean']:.3f} (n={m['base_n']})" if m["base_mean"] is not None else "-"
        new = f"{m['new_mean']:.3f} (n={m['new_n']})" if m["new_mean"] is not None else "-"
        delta = f"{m['delta']:+.3f}" if m["delta"] is not None else "-"
        print(f"{key:22s} {base:>14s} {new:>14s} {delta:>8s}")

    for label, key in (
        ("citation rate", "citation_rate"),
        ("citation validity", "citation_validity"),
        ("out-of-scope acc", "out_of_scope_accuracy"),
        ("not-in-syllabus acc", "not_in_syllabus_accuracy"),
        ("answer rate", "answer_rate"),
        ("page hit rate", "page_hit_rate"),
    ):
        if key not in data:
            continue
        pair = data[key]
        if key == "citation_rate":
            b, n = pair["base"]["rate"], pair["new"]["rate"]
            bn, nn = pair["base"]["answered"], pair["new"]["answered"]
        elif key == "citation_validity":
            b, n = pair["base"]["rate"], pair["new"]["rate"]
            bn, nn = pair["base"]["rows"], pair["new"]["rows"]
        elif key == "page_hit_rate":
            b, n = pair["base"]["rate"], pair["new"]["rate"]
            bn, nn = pair["base"]["total"], pair["new"]["total"]
        else:
            (b, bn), (n, nn) = pair["base"], pair["new"]
        bs = f"{b:.3f} (n={bn})" if b is not None else "-"
        ns = f"{n:.3f} (n={nn})" if n is not None else "-"
        print(f"{label:22s} {bs:>14s} {ns:>14s}")

    if data["changed_rows"]:
        print(f"\nrows that changed ({len(data['changed_rows'])}):")
        for row in data["changed_rows"]:
            bits = [f"{k} {v['base']}->{v['new']}" for k, v in row["moved"].items()]
            status = (
                f"status {row['base_status']}->{row['new_status']}" if row["status_changed"] else ""
            )
            flag = " [multi_chunk]" if row["multi_chunk"] else ""
            print(f"  {row['id']}{flag}: {', '.join(filter(None, [status, *bits]))}")

    print("\nper-metric row counts (higher / lower / unchanged):")
    for key, d in data["direction"].items():
        print(f"  {key:22s} {d['higher']} / {d['lower']} / {d['unchanged']}")


def run_compare(
    base_id: str,
    new_id: str,
    *,
    settings: Settings | None = None,
    reference_pages: dict[str, list[int]] | None = None,
) -> dict:
    settings = settings or get_settings()
    base_rows = load_scores(settings.runs_dir / base_id / "scores.csv")
    new_rows = load_scores(settings.runs_dir / new_id / "scores.csv")
    if not base_rows:
        raise FileNotFoundError(f"no scores.csv for base run {base_id}")
    if not new_rows:
        raise FileNotFoundError(f"no scores.csv for new run {new_id}")

    data = compare(base_rows, new_rows, reference_pages)
    data["base_run"] = base_id
    data["new_run"] = new_id
    out = settings.runs_dir / new_id / "comparison.json"
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    from istqb_rag.eval.plain_report import write_comparison

    plain = write_comparison(settings.runs_dir / new_id, base_id, data, settings.golden_path)
    print_comparison(data, base_id, new_id)
    print(f"\nwrote {out}")
    print(f"wrote {plain}")
    return data


def reference_pages_from_golden(path: Path) -> dict[str, list[int]]:
    """{id: reference_pages} for in-scope rows, read straight from the dataset."""
    pages = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("reference_pages"):
            pages[row["id"]] = row["reference_pages"]
    return pages
