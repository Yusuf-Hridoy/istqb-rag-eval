"""Metrics that need no judge: citation rate, citation validity and page hit rate."""


def citation_rate(rows: list[dict]) -> dict:
    """Share of answered in-scope rows that cite at least one page."""
    answered = [r for r in rows if r["type"] == "in_scope" and r["status"] == "answered"]
    cited = [r for r in answered if str(r.get("cited_pages", "")).strip()]
    return {
        "rate": round(len(cited) / len(answered), 4) if answered else None,
        "cited": len(cited),
        "answered": len(answered),
        "uncited_ids": [r["id"] for r in answered if not str(r.get("cited_pages", "")).strip()],
    }


def citation_validity(rows: list[dict]) -> dict:
    """Share of cited pages that appear among the row's retrieved pages.

    Structured mode lets the model report its own citations, so citation rate
    alone can rise from invented pages.
    """
    cited = [
        r
        for r in rows
        if r["type"] == "in_scope" and r["status"] == "answered" and _pages(r.get("cited_pages"))
    ]
    # Unrecorded retrieved pages are missing data, not invented citations: counted
    # apart so an old run does not read as 0.0.
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
    if not value:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {int(p) for p in value}
    return {int(p) for p in str(value).split(";") if p.strip().isdigit()}


def page_hit_rate(rows: list[dict], reference_pages: dict[str, list[int]]) -> dict:
    """Share of in-scope rows where a retrieved page is one of the reference pages."""
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
