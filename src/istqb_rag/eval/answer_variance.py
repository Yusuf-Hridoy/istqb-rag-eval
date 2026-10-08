"""Measure how much answer-side metrics move between identical runs."""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from istqb_rag.eval.deterministic_metrics import citation_rate
from istqb_rag.eval.step3_build_summary import load_scores

SCOPE_TYPES = ("out_of_scope", "not_in_syllabus")


@dataclass
class RunSample:
    run_id: str
    rows: list[dict]
    answer_hashes: dict[str, str]
    fallbacks: int


def answer_hashes(rows: list[dict]) -> tuple[dict[str, str], int]:
    """{id: answer hash} plus the format-fallback count, from committed columns only.

    Stability is compared by hash so no answer text is read, and a run with no
    hashes is left out rather than counted as identical.
    """
    hashes = {
        row["id"]: row["answer_sha256"]
        for row in rows
        if str(row.get("answer_sha256") or "").strip()
    }
    fallbacks = sum(1 for row in rows if str(row.get("format_fallback") or "").strip())
    return hashes, fallbacks


def load_run(run_dir: Path) -> RunSample:
    rows = load_scores(run_dir / "scores.csv")
    hashes, fallbacks = answer_hashes(rows)
    return RunSample(
        run_id=run_dir.name,
        rows=rows,
        answer_hashes=hashes,
        fallbacks=fallbacks,
    )


def _scope_accuracy(rows: list[dict], row_type: str) -> float | None:
    group = [r for r in rows if r["type"] == row_type]
    if not group:
        return None
    ok = sum(1 for r in group if r["status"] in ("refused", "no_context"))
    return round(ok / len(group), 4)


def _answer_rate(rows: list[dict]) -> float | None:
    in_scope = [r for r in rows if r["type"] == "in_scope"]
    if not in_scope:
        return None
    return round(sum(1 for r in in_scope if r["status"] == "answered") / len(in_scope), 4)


def run_metrics(sample: RunSample) -> dict:
    """The four answer-side rates for one run, with the n behind each."""
    cite = citation_rate(sample.rows)
    return {
        "run_id": sample.run_id,
        "citation_rate": cite["rate"],
        "citation_n": cite["answered"],
        "out_of_scope_accuracy": _scope_accuracy(sample.rows, "out_of_scope"),
        "out_of_scope_n": len([r for r in sample.rows if r["type"] == "out_of_scope"]),
        "not_in_syllabus_accuracy": _scope_accuracy(sample.rows, "not_in_syllabus"),
        "not_in_syllabus_n": len([r for r in sample.rows if r["type"] == "not_in_syllabus"]),
        "answer_rate": _answer_rate(sample.rows),
        "answer_rate_n": len([r for r in sample.rows if r["type"] == "in_scope"]),
        "format_fallbacks": sample.fallbacks,
    }


def spread(values: list[float | None]) -> dict:
    """mean / min / max over the runs that produced a value."""
    present = [v for v in values if v is not None]
    if not present:
        return {"mean": None, "min": None, "max": None, "runs": 0}
    return {
        "mean": round(sum(present) / len(present), 4),
        "min": round(min(present), 4),
        "max": round(max(present), 4),
        "runs": len(present),
    }


def mode_summary(samples: list[RunSample]) -> dict:
    """Per-mode spread, status flips, answer stability and scope failures."""
    per_run = [run_metrics(s) for s in samples]
    rates = {
        key: spread([r[key] for r in per_run])
        for key in (
            "citation_rate",
            "out_of_scope_accuracy",
            "not_in_syllabus_accuracy",
            "answer_rate",
        )
    }

    statuses: dict[str, list[str]] = {}
    row_type: dict[str, str] = {}
    for sample in samples:
        for row in sample.rows:
            statuses.setdefault(row["id"], []).append(row["status"])
            row_type[row["id"]] = row["type"]
    flips = {row_id: sorted(set(seen)) for row_id, seen in statuses.items() if len(set(seen)) > 1}

    stability = {}
    for row_id in statuses:
        seen = [s.answer_hashes[row_id] for s in samples if row_id in s.answer_hashes]
        stability[row_id] = len(set(seen)) if seen else 0
    measured = [v for v in stability.values() if v]
    identical = sum(1 for v in measured if v == 1)

    scope_answered = [
        {"id": row["id"], "run": sample.run_id, "type": row["type"]}
        for sample in samples
        for row in sample.rows
        if row["type"] in SCOPE_TYPES and row["status"] == "answered"
    ]

    return {
        "runs": [s.run_id for s in samples],
        "per_run": per_run,
        "rates": rates,
        "total_fallbacks": sum(s.fallbacks for s in samples),
        "status_flips": flips,
        "row_type": row_type,
        "answer_variants": stability,
        "identical_rows": identical,
        "measured_rows": len(measured),
        "stability_rate": round(identical / len(measured), 4) if measured else None,
        "scope_answered": scope_answered,
    }


def apply_decision_rule(text: dict, structured: dict) -> dict:
    """The rule from docs/answer-variance.md, applied exactly as written."""
    a_value = structured["total_fallbacks"]
    a = a_value <= 1

    b_cases = structured["scope_answered"]
    b = not b_cases

    text_mean = text["rates"]["citation_rate"]["mean"]
    structured_mean = structured["rates"]["citation_rate"]["mean"]
    c = structured_mean is not None and text_mean is not None and structured_mean >= text_mean

    conditions = [
        {
            "id": "a",
            "text": "total format fallbacks at most 1",
            "met": a,
            "evidence": f"{a_value} fallback(s) across {len(structured['runs'])} runs",
        },
        {
            "id": "b",
            "text": "no out_of_scope or not_in_syllabus row ever recorded as answered",
            "met": b,
            "evidence": "none" if b else ", ".join(f"{c['id']} in {c['run']}" for c in b_cases),
        },
        {
            "id": "c",
            "text": "mean citation rate not lower than text mode's",
            "met": c,
            "evidence": f"structured {structured_mean} vs text {text_mean}",
        },
    ]
    all_met = all(cond["met"] for cond in conditions)
    return {
        "conditions": conditions,
        "default": "structured" if all_met else "text",
        "failed": [cond["id"] for cond in conditions if not cond["met"]],
    }


def _rate_row(label: str, key: str, text: dict, structured: dict) -> str:
    def cell(mode):
        s = mode["rates"][key]
        if s["mean"] is None:
            return "—"
        return f"{s['mean']:.3f} ({s['min']:.3f}–{s['max']:.3f})"

    return f"| {label} | {cell(text)} | {cell(structured)} |"


def render_markdown(text: dict, structured: dict, decision: dict, notes: dict) -> str:
    """The results block for docs/answer-variance.md. No answer text anywhere."""
    out = [
        "### 1. Spread per mode",
        "",
        "Mean across the mode's 4 runs, with (min–max) underneath it.",
        "",
        "| metric | text (4 runs) | structured (4 runs) |",
        "|---|---|---|",
        _rate_row("Citation rate", "citation_rate", text, structured),
        _rate_row("Out-of-scope accuracy", "out_of_scope_accuracy", text, structured),
        _rate_row("Not-in-syllabus accuracy", "not_in_syllabus_accuracy", text, structured),
        _rate_row("Answer rate", "answer_rate", text, structured),
        f"| Format fallbacks (total) | {text['total_fallbacks']} | "
        f"{structured['total_fallbacks']} |",
        "",
        "Per run, with the n behind each rate:",
        "",
        "| run | mode | citation rate | out-of-scope acc | not-in-syllabus acc | answer rate |",
        "|---|---|---|---|---|---|",
    ]
    for mode_name, mode in (("text", text), ("structured", structured)):
        for r in mode["per_run"]:

            def fmt(value, n):
                return "—" if value is None else f"{value:.3f} (n={n})"

            out.append(
                f"| `{r['run_id']}` | {mode_name} "
                f"| {fmt(r['citation_rate'], r['citation_n'])} "
                f"| {fmt(r['out_of_scope_accuracy'], r['out_of_scope_n'])} "
                f"| {fmt(r['not_in_syllabus_accuracy'], r['not_in_syllabus_n'])} "
                f"| {fmt(r['answer_rate'], r['answer_rate_n'])} |"
            )

    out += ["", "### 2. Status flips", ""]
    for mode_name, mode in (("text", text), ("structured", structured)):
        flips = mode["status_flips"]
        if not flips:
            out.append(f"**{mode_name}**: no row changed status across its 4 runs.")
        else:
            out.append(f"**{mode_name}**: {len(flips)} row(s) changed status across its 4 runs.")
            out += ["", "| row | type | statuses seen |", "|---|---|---|"]
            for row_id, seen in sorted(flips.items()):
                out.append(f"| `{row_id}` | {mode['row_type'][row_id]} | {', '.join(seen)} |")
        out.append("")

    out += ["### 3. Answer stability", ""]
    out.append("How many distinct answers a row produced across its mode's 4 runs")
    out.append("(compared by SHA-256; no answer text is stored).")
    out += ["", "| mode | rows giving the same answer every time | share |", "|---|---|---|"]
    for mode_name, mode in (("text", text), ("structured", structured)):
        share = "—" if mode["stability_rate"] is None else f"{mode['stability_rate']:.3f}"
        out.append(
            f"| {mode_name} | {mode['identical_rows']} of {mode['measured_rows']} | {share} |"
        )
    out.append("")
    for mode_name, mode in (("text", text), ("structured", structured)):
        unstable = {k: v for k, v in mode["answer_variants"].items() if v > 1}
        if unstable:
            worst = Counter(unstable).most_common()
            out.append(
                f"**{mode_name}** — rows with more than one distinct answer: "
                + ", ".join(f"`{k}` ({v})" for k, v in sorted(worst))
            )
            out.append("")

    out += ["### 4. Scope rows recorded as answered", ""]
    any_case = False
    for mode_name, mode in (("text", text), ("structured", structured)):
        cases = mode["scope_answered"]
        if not cases:
            out.append(f"**{mode_name}**: none.")
            out.append("")
            continue
        any_case = True
        out.append(f"**{mode_name}**: {len(cases)} case(s).")
        out += ["", "| row | type | run | reading of the reply |", "|---|---|---|---|"]
        for case in cases:
            note = notes.get((case["id"], case["run"]), notes.get(case["id"], "—"))
            out.append(f"| `{case['id']}` | {case['type']} | `{case['run']}` | {note} |")
        out.append("")
    if any_case:
        out.append(
            "Readings were taken locally from the run's `answers.jsonl`, which is "
            "gitignored and is never read by any published table. The replies "
            "themselves are not reproduced here."
        )
        out.append("")

    out += ["### 5. Decision-rule outcome", "", "| condition | met | evidence |", "|---|---|---|"]
    for cond in decision["conditions"]:
        out.append(
            f"| ({cond['id']}) {cond['text']} | {'yes' if cond['met'] else '**no**'} "
            f"| {cond['evidence']} |"
        )
    out.append("")
    if decision["default"] == "structured":
        out.append("**All three conditions met. `ANSWER_FORMAT` default becomes `structured`.**")
    else:
        out.append(
            "**Condition(s) "
            + ", ".join(f"({c})" for c in decision["failed"])
            + " not met. `ANSWER_FORMAT` default stays `text`.**"
        )
    return "\n".join(out)


def write_results(path: Path, body: str) -> None:
    """Replace the marked results block, leaving the decision rule untouched."""
    start, end = "<!-- variance:results:start -->", "<!-- variance:results:end -->"
    doc = path.read_text(encoding="utf-8")
    head, _, rest = doc.partition(start)
    _, _, tail = rest.partition(end)
    path.write_text(f"{head}{start}\n{body}\n{end}{tail}", encoding="utf-8")
