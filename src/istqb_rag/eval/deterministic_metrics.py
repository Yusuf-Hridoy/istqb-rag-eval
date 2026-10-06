"""Metrics that need no judge: citation rate, page hit rate, and Cohen's kappa.

Every number here is computed from data already on disk, so it costs nothing and
is identical on every rerun. Phase 3's judge budget is 15 calls for the whole
phase, so anything a deterministic check can answer is answered here instead.
"""

import math


def citation_rate(rows: list[dict]) -> dict:
    """Share of answered in-scope rows that cite at least one page.

    Works on an old scores.csv too: the column has always been ``cited_pages``,
    so pilot-1 can be measured retroactively without being re-run.
    """
    answered = [r for r in rows if r["type"] == "in_scope" and r["status"] == "answered"]
    cited = [r for r in answered if str(r.get("cited_pages", "")).strip()]
    return {
        "rate": round(len(cited) / len(answered), 4) if answered else None,
        "cited": len(cited),
        "answered": len(answered),
        "uncited_ids": [r["id"] for r in answered if not str(r.get("cited_pages", "")).strip()],
    }


def citation_validity(rows: list[dict]) -> dict:
    """Share of cited pages that actually appear among the row's retrieved pages.

    Structured mode lets the model report its own cited pages, so citation
    rate alone can rise from invented citations. This checks each cited page
    against the pages retrieval actually returned — deterministic, no judge.
    Computed over answered in-scope rows that cite at least one page; a row
    with nothing cited has nothing to validate.
    """
    cited = [
        r
        for r in rows
        if r["type"] == "in_scope" and r["status"] == "answered" and _pages(r.get("cited_pages"))
    ]
    # A row whose retrieved pages were never recorded cannot be validated. Runs
    # from before the retrieved_pages column existed would otherwise read as
    # 0.0 — "every citation invented" — which is missing data, not a finding.
    unknown_ids = [r["id"] for r in cited if not _pages(r.get("retrieved_pages"))]
    checkable = [r for r in cited if _pages(r.get("retrieved_pages"))]
    if not checkable:
        return {
            "rate": None,
            "rows": 0,
            "valid_pages": 0,
            "total_pages": 0,
            "invalid_ids": [],
            "unknown_ids": unknown_ids,
            "note": "no run data recorded which pages were retrieved" if unknown_ids else "",
        }
    per_row = []
    valid = total = 0
    invalid_ids = []
    for row in checkable:
        pages = _pages(row.get("cited_pages"))
        got = _pages(row.get("retrieved_pages"))
        row_valid = len(pages & got)
        per_row.append(row_valid / len(pages))
        valid += row_valid
        total += len(pages)
        if row_valid < len(pages):
            invalid_ids.append(row["id"])
    return {
        "rate": round(sum(per_row) / len(per_row), 4),
        "rows": len(checkable),
        "valid_pages": valid,
        "total_pages": total,
        "invalid_ids": invalid_ids,
        "unknown_ids": unknown_ids,
        "note": f"{len(unknown_ids)} row(s) had no retrieved pages recorded" if unknown_ids else "",
    }


def _pages(value: object) -> set[int]:
    """Parse a ';'-separated page cell into a set of ints."""
    if not value:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {int(p) for p in value}
    return {int(p) for p in str(value).split(";") if p.strip().isdigit()}


def page_hit_rate(rows: list[dict], reference_pages: dict[str, list[int]]) -> dict:
    """Share of in-scope rows where a retrieved page is one of the reference pages.

    This is the free counterpart to context recall: it asks whether retrieval
    reached the right part of the syllabus at all, without asking a judge
    whether the text supports the answer.
    """
    scored = [
        r
        for r in rows
        if r["type"] == "in_scope" and r["status"] != "error" and r["id"] in reference_pages
    ]
    hits, misses = 0, []
    for row in scored:
        wanted = set(reference_pages[row["id"]])
        got = _pages(row.get("retrieved_pages"))
        if got & wanted:
            hits += 1
        else:
            misses.append(row["id"])
    return {
        "rate": round(hits / len(scored), 4) if scored else None,
        "hits": hits,
        "total": len(scored),
        "missed_ids": misses,
    }


def cohens_kappa(pairs: list[tuple[str, str]]) -> dict:
    """Cohen's kappa for two raters over the same items.

    kappa = (po - pe) / (1 - pe), computed by hand rather than pulling in
    scikit-learn for five lines of arithmetic.

    When both raters give every item the same single label, pe is 1 and the
    formula divides by zero. Kappa is undefined there — agreement carries no
    information if there was never a choice to disagree about — so ``kappa`` is
    None rather than 0 or 1.
    """
    n = len(pairs)
    if n == 0:
        return {"n": 0, "percent_agreement": None, "kappa": None, "note": "no overlapping rows"}

    agreed = sum(1 for a, b in pairs if a == b)
    po = agreed / n

    labels = {label for pair in pairs for label in pair}
    pe = 0.0
    for label in labels:
        pe += (sum(1 for a, _ in pairs if a == label) / n) * (
            sum(1 for _, b in pairs if b == label) / n
        )

    if math.isclose(pe, 1.0):
        return {
            "n": n,
            "percent_agreement": round(po, 4),
            "kappa": None,
            "note": "kappa undefined: both raters used a single label (pe = 1)",
        }
    return {
        "n": n,
        "percent_agreement": round(po, 4),
        "kappa": round((po - pe) / (1 - pe), 4),
        "expected_agreement": round(pe, 4),
        "note": "",
    }


def confusion_matrix(pairs: list[tuple[str, str]]) -> dict:
    """2x2 counts for yes/no pairs, as (human, judge)."""
    counts = {"both_yes": 0, "both_no": 0, "human_yes_judge_no": 0, "human_no_judge_yes": 0}
    for human, judge in pairs:
        if human == "yes" and judge == "yes":
            counts["both_yes"] += 1
        elif human == "no" and judge == "no":
            counts["both_no"] += 1
        elif human == "yes":
            counts["human_yes_judge_no"] += 1
        else:
            counts["human_no_judge_yes"] += 1
    return counts


def backfill_retrieved_pages(rows: list[dict], answers_path) -> list[dict]:
    """Fill in retrieved_pages from a run's answers.jsonl when the column predates it.

    pilot-1 was scored before scores.csv carried retrieved pages. The answers
    file still has them, so the run can be measured retroactively without being
    re-run or rewritten. Rows are copied, never mutated in place.
    """
    import json
    from pathlib import Path

    path = Path(answers_path)
    if not path.exists():
        return rows
    pages_by_id = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        contexts = record.get("result", {}).get("contexts", [])
        pages_by_id[record["id"]] = ";".join(
            str(p) for p in dict.fromkeys(c["page"] for c in contexts)
        )
    out = []
    for row in rows:
        if not str(row.get("retrieved_pages") or "").strip() and row["id"] in pages_by_id:
            row = {**row, "retrieved_pages": pages_by_id[row["id"]]}
        out.append(row)
    return out
